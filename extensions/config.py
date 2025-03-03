from __future__ import annotations

from collections import defaultdict
from itertools import accumulate
from typing import TYPE_CHECKING

import asyncpg
import discord
from discord.ext import commands, menus

from utilities.shared import cache, checks
from utilities.shared.paginator import RoboPages, SimplePages

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable

    from asyncpg import Connection, Pool, Record

    from bot import Mipha
    from utilities.context import Context, GuildContext


async def plonk_iterator(bot: Mipha, guild: discord.Guild, records: list[Record]) -> AsyncIterator[str]:
    for record in records:
        entity_id: int = record[0]
        resolved = guild.get_channel(entity_id) or await bot.get_or_fetch_member(guild, entity_id)
        if resolved is None:
            yield f"<Not Found: {entity_id}>"
        yield str(resolved)


class PlonkedPageSource(menus.AsyncIteratorPageSource):
    def __init__(self, bot: Mipha, guild: discord.Guild, records: list[Record]) -> None:
        super().__init__(plonk_iterator(bot, guild, records), per_page=20)

    async def format_page(self, menu: RoboPages, entries: list[str]) -> discord.Embed:
        embed = discord.Embed(colour=discord.Colour.blurple())
        pages = []
        for index, entry in enumerate(entries, start=menu.current_page * self.per_page):
            pages.append(f"{index + 1}. {entry}")

        embed.description = "\n".join(pages)
        return embed


class ChannelOrMember(commands.Converter):
    async def convert(self, ctx: GuildContext, argument: str) -> discord.TextChannel | discord.Member:
        try:
            return await commands.TextChannelConverter().convert(ctx, argument)
        except commands.BadArgument:
            return await commands.MemberConverter().convert(ctx, argument)


class CommandName(commands.Converter):
    async def convert(self, ctx: Context, argument: str) -> str:
        lowered = argument.lower()

        command = ctx.bot.get_command(lowered)

        if command is None or command.cog_name in ("Config", "Admin"):
            msg = f"Command {lowered!r} is not valid."
            raise commands.BadArgument(msg)

        return command.qualified_name


class ResolvedCommandPermissions:
    class _Entry:
        __slots__ = ("allow", "deny")

        def __init__(self) -> None:
            self.allow: set[str] = set()
            self.deny: set[str] = set()

    def __init__(self, guild_id: int, records: list[tuple[str, int, bool]]) -> None:
        self.guild_id: int = guild_id

        self._lookup: defaultdict[int | None, ResolvedCommandPermissions._Entry] = defaultdict(self._Entry)

        for name, channel_id, whitelist in records:
            entry = self._lookup[channel_id]
            if whitelist:
                entry.allow.add(name)
            else:
                entry.deny.add(name)

    def _split(self, obj: str) -> list[str]:
        # "hello there world" -> ["hello", "hello there", "hello there world"]
        return list(accumulate(obj.split(), lambda x, y: f"{x} {y}"))

    def get_blocked_commands(self, channel_id: int) -> set[str]:
        if len(self._lookup) == 0:
            return set()

        guild = self._lookup[None]
        channel = self._lookup[channel_id]

        # first, apply the guild-level denies
        ret = guild.deny - guild.allow

        # then apply the channel-level denies
        return ret | (channel.deny - channel.allow)

    def _is_command_blocked(self, name: str, channel_id: int) -> bool | None:
        command_names = self._split(name)

        guild = self._lookup[None]  # no special channel_id
        channel = self._lookup[channel_id]

        blocked = None

        # apply guild-level denies first
        # then guild-level allow
        # then channel-level deny
        # then channel-level allow

        # use ?foo bar
        # ?foo bar <- guild allow
        # ?foo <- channel block
        # result: blocked  # noqa: ERA001
        # this is why the two for loops are separate

        for command in command_names:
            if command in guild.deny:
                blocked = True

            if command in guild.allow:
                blocked = False

        for command in command_names:
            if command in channel.deny:
                blocked = True

            if command in channel.allow:
                blocked = False

        return blocked

    def is_command_blocked(self, name: str, channel_id: int) -> bool | None:
        # fast path
        if len(self._lookup) == 0:
            return False
        return self._is_command_blocked(name, channel_id)

    def is_blocked(self, ctx: Context) -> bool | None:
        # fast path
        if len(self._lookup) == 0:
            return False

        if isinstance(ctx.author, discord.Member) and ctx.author.guild_permissions.manage_guild:
            return False

        return self._is_command_blocked(ctx.command.qualified_name, ctx.channel.id)


class Config(commands.Cog):
    """Handles the bot's configuration system.

    This is how you disable or enable certain commands
    for your server or block certain channels or members.
    """

    def __init__(self, bot: Mipha) -> None:
        self.bot: Mipha = bot

    @property
    def display_emoji(self) -> discord.PartialEmoji:
        return discord.PartialEmoji(name="\N{GEAR}\ufe0f")

    @cache.cache(strategy=cache.Strategy.lru, maxsize=1024, ignore_kwargs=True)
    async def is_plonked(
        self,
        guild_id: int,
        member_id: int,
        channel: discord.VoiceChannel | discord.TextChannel | discord.Thread | None = None,
        *,
        connection: Connection | Pool | None = None,
        check_bypass: bool = True,
    ) -> bool:
        if member_id in self.bot._blacklist_data or guild_id in self.bot._blacklist_data:
            return True

        if check_bypass:
            guild = self.bot.get_guild(guild_id)
            if guild is not None:
                member = await self.bot.get_or_fetch_member(guild, member_id)
                if member is not None and member.guild_permissions.manage_guild:
                    return False

        connection = connection or self.bot.pool

        if channel is None:
            query = "SELECT 1 FROM plonks WHERE guild_id=$1 AND entity_id=$2;"
            row = await connection.fetchrow(query, guild_id, member_id)
        else:
            if isinstance(channel, discord.Thread):
                query = "SELECT 1 FROM plonks WHERE guild_id=$1 AND entity_id IN ($2, $3, $4);"
                row = await connection.fetchrow(query, guild_id, member_id, channel.id, channel.parent_id)
            else:
                query = "SELECT 1 FROM plonks WHERE guild_id=$1 AND entity_id IN ($2, $3);"
                row = await connection.fetchrow(query, guild_id, member_id, channel.id)

        return row is not None

    async def bot_check_once(self, ctx: Context) -> bool:
        if ctx.guild is None:
            return True

        is_owner = await ctx.bot.is_owner(ctx.author)
        if is_owner:
            return True

        # see if they can bypass:
        if isinstance(ctx.author, discord.Member):
            bypass = ctx.author.guild_permissions.manage_guild
            if bypass:
                return True

        # check if we're plonked
        is_plonked = await self.is_plonked(ctx.guild.id, ctx.author.id, channel=ctx.channel, check_bypass=False)

        return not is_plonked

    @cache.cache()
    async def get_command_permissions(
        self,
        guild_id: int,
        *,
        connection: Connection | Pool | None = None,
    ) -> ResolvedCommandPermissions:
        connection = connection or self.bot.pool
        query = "SELECT name, channel_id, whitelist FROM command_config WHERE guild_id=$1;"

        records = await connection.fetch(query, guild_id)
        return ResolvedCommandPermissions(guild_id, records)

    async def bot_check(self, ctx: Context) -> bool:
        if ctx.guild is None:
            return True

        is_owner = await ctx.bot.is_owner(ctx.author)
        if is_owner:
            return True

        resolved = await self.get_command_permissions(ctx.guild.id)
        return not resolved.is_blocked(ctx)

    async def _bulk_ignore_entries(self, ctx: GuildContext, entries: Iterable[discord.abc.Snowflake]) -> None:
        async with ctx.db.acquire() as con, con.transaction():
            query = "SELECT entity_id FROM plonks WHERE guild_id=$1;"
            records = await con.fetch(query, ctx.guild.id)

            # we do not want to insert duplicates
            current_plonks = {r[0] for r in records}
            guild_id = ctx.guild.id
            to_insert = [(guild_id, e.id) for e in entries if e.id not in current_plonks]

            # do a bulk COPY
            await con.copy_records_to_table("plonks", columns=("guild_id", "entity_id"), records=to_insert)

            # invalidate the cache for this guild
            self.is_plonked.invalidate_containing(f"{ctx.guild.id!r}:")

    async def cog_command_error(self, ctx: Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.BadArgument):
            await ctx.send(str(error))

    @commands.group()
    async def config(self, ctx: Context) -> None:
        """Handles the server or channel permission configuration for the bot."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help("config")

    @config.group(invoke_without_command=True, aliases=["plonk"])
    @checks.is_mod()
    async def ignore(
        self,
        ctx: GuildContext,
        *entities: discord.TextChannel | discord.Member | discord.VoiceChannel,
    ) -> None:
        """Ignores text channels or members from using the bot.

        If no channel or member is specified, the current channel is ignored.

        Users with Administrator can still use the bot, regardless of ignore
        status.

        To use this command you must have Ban Members and Manage Messages permissions.
        """

        if len(entities) == 0:
            # shortcut for a single insert
            query = "INSERT INTO plonks (guild_id, entity_id) VALUES ($1, $2) ON CONFLICT DO NOTHING;"
            await ctx.db.execute(query, ctx.guild.id, ctx.channel.id)

            # invalidate the cache for this guild
            self.is_plonked.invalidate_containing(f"{ctx.guild.id!r}:")
        else:
            await self._bulk_ignore_entries(ctx, entities)

        await ctx.send(ctx.tick(True))  # noqa: FBT003 # shortcut

    @ignore.command(name="list")
    @checks.is_mod()
    @commands.cooldown(2, 60.0, commands.BucketType.guild)
    async def ignore_list(self, ctx: GuildContext) -> None:
        """Tells you what channels or members are currently ignored in this server.

        To use this command you must have Ban Members and Manage Messages permissions.
        """

        query = "SELECT entity_id FROM plonks WHERE guild_id=$1;"

        guild = ctx.guild
        records = await ctx.db.fetch(query, guild.id)

        if len(records) == 0:
            await ctx.send("I am not ignoring anything here.")
            return

        source = PlonkedPageSource(self.bot, guild, records)
        pages = RoboPages(source, ctx=ctx)
        await pages.start()

    @ignore.command(name="all")
    @checks.is_mod()
    async def _all(self, ctx: GuildContext) -> None:
        """Ignores every channel in the server from being processed.

        This works by adding every channel that the server currently has into
        the ignore list. If more channels are added then they will have to be
        ignored by using the ignore command.

        To use this command you must have Ban Members and Manage Messages permissions.
        """
        await self._bulk_ignore_entries(ctx, ctx.guild.text_channels)
        await ctx.send("Successfully blocking all channels here.")

    @ignore.command(name="clear")
    @checks.is_mod()
    async def ignore_clear(self, ctx: GuildContext) -> None:
        """Clears all the currently set ignores.

        To use this command you must have Ban Members and Manage Messages permissions.
        """

        query = "DELETE FROM plonks WHERE guild_id=$1;"
        await ctx.db.execute(query, ctx.guild.id)
        self.is_plonked.invalidate_containing(f"{ctx.guild.id!r}:")
        await ctx.send("Successfully cleared all ignores.")

    @config.group(pass_context=True, invoke_without_command=True, aliases=["unplonk"])
    @checks.is_mod()
    async def unignore(
        self,
        ctx: GuildContext,
        *entities: discord.TextChannel | discord.Member | discord.VoiceChannel,
    ) -> None:
        """Allows channels or members to use the bot again.

        If nothing is specified, it unignores the current channel.

        To use this command you must have Ban Members and Manage Messages permissions.
        """

        if len(entities) == 0:
            query = "DELETE FROM plonks WHERE guild_id=$1 AND entity_id=$2;"
            await ctx.db.execute(query, ctx.guild.id, ctx.channel.id)
        else:
            query = "DELETE FROM plonks WHERE guild_id=$1 AND entity_id = ANY($2::bigint[]);"
            entity_ids = [c.id for c in entities]
            await ctx.db.execute(query, ctx.guild.id, entity_ids)

        self.is_plonked.invalidate_containing(f"{ctx.guild.id!r}:")
        await ctx.send(ctx.tick(True))  # noqa: FBT003 # shortcut

    @unignore.command(name="all")
    @checks.is_mod()
    async def unignore_all(self, ctx: GuildContext) -> None:
        """An alias for ignore clear command."""
        await ctx.invoke(self.ignore_clear)

    @config.group(aliases=["guild"])
    @checks.is_mod()
    async def server(self, ctx: GuildContext) -> None:
        """Handles the server-specific permissions."""

    @config.group()
    @checks.is_mod()
    async def channel(self, ctx: GuildContext) -> None:
        """Handles the channel-specific permissions."""

    async def command_toggle(
        self,
        pool: Pool,
        guild_id: int,
        channel_id: int | None,
        name: str,
        *,
        whitelist: bool = True,
    ) -> None:
        # clear the cache
        self.get_command_permissions.invalidate(self, guild_id)

        if channel_id is None:
            subcheck = "channel_id IS NULL"
            args = (guild_id, name)
        else:
            subcheck = "channel_id=$3"
            args = (guild_id, name, channel_id)

        async with pool.acquire() as connection, connection.transaction():
            # delete the previous entry regardless of what it was
            query = f"DELETE FROM command_config WHERE guild_id=$1 AND name=$2 AND {subcheck};"  # noqa: S608 # our data only

            # DELETE <num>
            await connection.execute(query, *args)

            query = "INSERT INTO command_config (guild_id, channel_id, name, whitelist) VALUES ($1, $2, $3, $4);"

            try:
                await connection.execute(query, guild_id, channel_id, name, whitelist)
            except asyncpg.UniqueViolationError as err:
                msg = "This command is already disabled." if not whitelist else "This command is already explicitly enabled."
                raise RuntimeError(msg) from err

    @channel.command(name="disable")
    async def channel_disable(self, ctx: GuildContext, *, command: str = commands.param(converter=CommandName)) -> None:
        """Disables a command for this channel."""

        try:
            await self.command_toggle(ctx.pool, ctx.guild.id, ctx.channel.id, command, whitelist=False)
        except RuntimeError as e:
            await ctx.send(str(e))
        else:
            await ctx.send("Command successfully disabled for this channel.")

    @channel.command(name="enable")
    async def channel_enable(self, ctx: GuildContext, *, command: str = commands.param(converter=CommandName)) -> None:
        """Enables a command for this channel."""

        try:
            await self.command_toggle(ctx.pool, ctx.guild.id, ctx.channel.id, command, whitelist=True)
        except RuntimeError as e:
            await ctx.send(str(e))
        else:
            await ctx.send("Command successfully enabled for this channel.")

    @server.command(name="disable")
    async def server_disable(self, ctx: GuildContext, *, command: str = commands.param(converter=CommandName)) -> None:
        """Disables a command for this server."""

        try:
            await self.command_toggle(ctx.pool, ctx.guild.id, None, command, whitelist=False)
        except RuntimeError as e:
            await ctx.send(str(e))
        else:
            await ctx.send("Command successfully disabled for this server")

    @server.command(name="enable")
    async def server_enable(self, ctx: GuildContext, *, command: str = commands.param(converter=CommandName)) -> None:
        """Enables a command for this server."""

        try:
            await self.command_toggle(ctx.pool, ctx.guild.id, None, command, whitelist=True)
        except RuntimeError as e:
            await ctx.send(str(e))
        else:
            await ctx.send("Command successfully enabled for this server.")

    @config.command(name="enable")
    @checks.is_mod()
    async def config_enable(
        self,
        ctx: GuildContext,
        channel: discord.TextChannel | None,
        *,
        command: str = commands.param(converter=CommandName),
    ) -> None:
        """Enables a command the server or a channel."""

        channel_id = channel.id if channel else None
        human_friendly = channel.mention if channel else "the server"
        try:
            await self.command_toggle(ctx.pool, ctx.guild.id, channel_id, command, whitelist=True)
        except RuntimeError as e:
            await ctx.send(str(e))
        else:
            await ctx.send(f"Command successfully enabled for {human_friendly}.")

    @config.command(name="disable")
    @checks.is_mod()
    async def config_disable(
        self,
        ctx: GuildContext,
        channel: discord.TextChannel | None,
        *,
        command: str = commands.param(converter=CommandName),
    ) -> None:
        """Disables a command for the server or a channel."""

        channel_id = channel.id if channel else None
        human_friendly = channel.mention if channel else "the server"
        try:
            await self.command_toggle(ctx.pool, ctx.guild.id, channel_id, command, whitelist=False)
        except RuntimeError as e:
            await ctx.send(str(e))
        else:
            await ctx.send(f"Command successfully disabled for {human_friendly}.")

    @config.command(name="disabled")
    @checks.is_mod()
    async def config_disabled(
        self,
        ctx: GuildContext,
        *,
        channel: discord.TextChannel | discord.VoiceChannel | None = None,
    ) -> None:
        """Shows the disabled commands for the channel given."""

        channel_id: int
        if channel is None:
            channel_id = ctx.channel.parent_id if isinstance(ctx.channel, discord.Thread) else ctx.channel.id
        else:
            channel_id = channel.id

        resolved = await self.get_command_permissions(ctx.guild.id)
        disabled = list(resolved.get_blocked_commands(channel_id))
        pages = SimplePages(disabled, ctx=ctx, per_page=15)
        await pages.start(content=f"In <#{channel_id}> the following commands are disabled")

    @config.group(name="global")
    @commands.is_owner()
    async def _global(self, ctx: GuildContext) -> None:
        """Handles global bot configuration."""

    @_global.command(name="block")
    async def global_block(self, ctx: GuildContext, object_id: int) -> None:
        """Blocks a user or guild globally."""
        await self.bot._blacklist_add(object_id)
        await ctx.send(ctx.tick(True))  # noqa: FBT003 # shortcut

    @_global.command(name="unblock")
    async def global_unblock(self, ctx: GuildContext, object_id: int) -> None:
        """Unblocks a user or guild globally."""
        await self.bot._blacklist_remove(object_id)
        await ctx.send(ctx.tick(True))  # noqa: FBT003 # shortcut


async def setup(bot: Mipha) -> None:
    await bot.add_cog(Config(bot))
