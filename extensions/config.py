"""
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at http://mozilla.org/MPL/2.0/.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from itertools import accumulate
from typing import TYPE_CHECKING

import asyncpg
import discord
from discord.ext import commands, menus

from utilities import cache, checks
from utilities.context import Context
from utilities.paginator import SimplePages


if TYPE_CHECKING:
    from bot import Mipha


class LazyEntity:
    """This is meant for use with the Paginator.

    It lazily computes __str__ when requested and
    caches it so it doesn't do the lookup again.
    """

    __slots__ = (
        "entity_id",
        "guild",
        "_cache",
    )

    def __init__(self, guild: discord.Guild, entity_id: int) -> None:
        self.entity_id = entity_id
        self.guild = guild
        self._cache = None

    def __str__(self) -> str:
        if self._cache:
            return self._cache

        entity = self.entity_id
        guild = self.guild
        resolved = guild.get_channel(entity) or guild.get_member(entity)
        if resolved is None:
            self._cache = f"<Not Found: {entity}>"
        else:
            self._cache = resolved.mention
        return self._cache


class CommandName(commands.Converter[str]):
    """Command name converter."""

    async def convert(self, ctx: Context, argument: str) -> str:
        """Perform conversion."""
        lowered = argument.lower()

        valid_commands = {c.qualified_name for c in ctx.bot.walk_commands() if c.cog_name not in ("Config", "Admin")}

        if lowered not in valid_commands:
            raise commands.BadArgument(f"Command {lowered!r} is not valid.")

        return lowered


class ResolvedCommandPermissions:
    """Resolves command permissions."""

    class _Entry:
        """Quick entry hack."""

        __slots__ = (
            "allow",
            "deny",
        )

        def __init__(self) -> None:
            self.allow = set()
            self.deny = set()

    def __init__(self, guild_id: int, records: list[asyncpg.Record]) -> None:
        self.guild_id = guild_id

        self._lookup = defaultdict(self._Entry)

        # channel_id: { allow: [commands], deny: [commands] }

        for name, channel_id, whitelist in records:
            entry = self._lookup[channel_id]
            if whitelist:
                entry.allow.add(name)
            else:
                entry.deny.add(name)

    def _split(self, obj: str) -> list[str]:
        """Splits objects into list."""
        # "hello there world" -> ["hello", "hello there", "hello there world"]
        return list(accumulate(obj.split(), lambda x, y: f"{x} {y}"))

    def get_blocked_commands(self, channel_id: int) -> set[str]:
        """Gets the blocked command."""
        if not self._lookup:
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
        # result: blocked
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
        """ " Is the command blocked?"""
        # fast path
        if not self._lookup:
            return False
        return self._is_command_blocked(name, channel_id)

    def is_blocked(self, ctx: Context) -> bool | None:
        """It is blocked!"""
        assert ctx.command is not None

        # fast path
        if not self._lookup:
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

    @cache.cache(strategy=cache.Strategy.lru, maxsize=1024, ignore_kwargs=True)
    async def is_plonked(
        self,
        guild_id: int,
        member_id: int,
        channel_id: int | None = None,
        *,
        connection: asyncpg.Connection | asyncpg.Pool | None = None,
        check_bypass: bool = True,
    ) -> bool:
        """They are plonked!"""
        if member_id in self.bot._blacklist_data or guild_id in self.bot._blacklist_data:
            return True

        if check_bypass:
            guild = self.bot.get_guild(guild_id)
            if guild is not None:
                member = guild.get_member(member_id)
                if member is not None and member.guild_permissions.manage_guild:
                    return False

        connection = connection or self.bot.pool

        if channel_id is None:
            query = "SELECT 1 FROM plonks WHERE guild_id=$1 AND entity_id=$2;"
            row = await connection.fetchrow(query, guild_id, member_id)
        else:
            query = "SELECT 1 FROM plonks WHERE guild_id=$1 AND entity_id IN ($2, $3);"
            row = await connection.fetchrow(query, guild_id, member_id, channel_id)

        return row is not None

    async def bot_check_once(self, ctx: Context) -> bool:
        """Performs a check only once for the cog."""
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
        is_plonked = await self.is_plonked(  # type: ignore # typing is gay with instance bindings
            ctx.guild.id,
            ctx.author.id,
            channel_id=ctx.channel.id,
            connection=ctx.db,
            check_bypass=False,
        )

        return not is_plonked

    @cache.cache()
    async def get_command_permissions(
        self, guild_id: int, *, connection: asyncpg.Connection | asyncpg.Pool | None = None
    ) -> ResolvedCommandPermissions:
        """Get command permissions."""
        connection = connection or self.bot.pool
        query = """
                SELECT name, channel_id, whitelist
                FROM command_config WHERE guild_id = $1;
                """

        records = await connection.fetch(query, guild_id)
        return ResolvedCommandPermissions(guild_id, records)

    async def bot_check(self, ctx: Context) -> bool:
        if ctx.guild is None:
            return True

        is_owner = await ctx.bot.is_owner(ctx.author)
        if is_owner:
            return True

        resolved = await self.get_command_permissions(ctx.guild.id, connection=ctx.db)  # type: ignore # typing is gay with instance bindings
        return not resolved.is_blocked(ctx)

    async def _bulk_ignore_entries(
        self,
        ctx: Context,
        entries: Iterable[discord.TextChannel | discord.Member],
    ) -> None:
        assert ctx.guild is not None

        async with ctx.db.acquire() as con:
            async with con.transaction():
                query = """
                        SELECT entity_id
                        FROM plonks
                        WHERE guild_id = $1;
                        """
                records = await ctx.db.fetch(query, ctx.guild.id)

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
    @checks.is_mod()
    async def config(self, ctx: Context) -> None:
        """Handles the server or channel permission configuration for the bot."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help("config")

    @config.group(invoke_without_command=True)
    @checks.is_mod()
    async def ignore(self, ctx: Context, *entities: discord.TextChannel | discord.Member) -> None:
        """Ignores text channels or members from using the bot.

        If no channel or member is specified, the current channel is ignored.

        Users with Manage Server can still use the bot, regardless of ignore
        status.

        To use this command you must have Manage Server permissions.
        """
        assert ctx.guild is not None

        if not entities:
            # shortcut for a single insert
            query = "INSERT INTO plonks (guild_id, entity_id) VALUES ($1, $2) ON CONFLICT DO NOTHING;"
            await ctx.db.execute(query, ctx.guild.id, ctx.channel.id)

            # invalidate the cache for this guild
            self.is_plonked.invalidate_containing(f"{ctx.guild.id!r}:")
        else:
            await self._bulk_ignore_entries(ctx, entities)

        await ctx.send(ctx.tick(True))

    @ignore.command(name="list")
    @checks.is_mod()
    @commands.cooldown(2, 60.0, commands.BucketType.guild)
    async def ignore_list(self, ctx: Context) -> None:
        """Tells you what channels or members are currently ignored in this server.

        To use this command you must have Manage Server permissions.
        """
        assert ctx.guild is not None

        query = "SELECT entity_id FROM plonks WHERE guild_id=$1;"

        guild = ctx.guild
        records = await ctx.db.fetch(query, guild.id)

        if not records:
            await ctx.send("I am not ignoring anything here.")
            return

        entries = [LazyEntity(guild, r[0]) for r in records]

        try:
            page = SimplePages(entries=entries, ctx=ctx, per_page=20)
            await page.start()
        except menus.MenuError as err:
            await ctx.send(str(err))

    @ignore.command(name="all")
    @checks.is_mod()
    async def _all(self, ctx: Context) -> None:
        """Ignores every channel in the server from being processed.

        This works by adding every channel that the server currently has into
        the ignore list. If more channels are added then they will have to be
        ignored by using the ignore command.

        To use this command you must have Manage Server permissions.
        """
        assert ctx.guild is not None

        await self._bulk_ignore_entries(ctx, ctx.guild.text_channels)
        await ctx.send("Successfully blocking all channels here.")

    @ignore.command(name="clear")
    @checks.is_mod()
    async def ignore_clear(self, ctx: Context) -> None:
        """Clears all the currently set ignores.

        To use this command you must have Manage Server permissions.
        """
        assert ctx.guild is not None

        query = "DELETE FROM plonks WHERE guild_id=$1;"
        await ctx.db.execute(query, ctx.guild.id)
        self.is_plonked.invalidate_containing(f"{ctx.guild.id!r}:")
        await ctx.send("Successfully cleared all ignores.")

    @config.group(pass_context=True, invoke_without_command=True)
    @checks.is_mod()
    async def unignore(self, ctx: Context, *entities: discord.TextChannel | discord.Member) -> None:
        """Allows channels or members to use the bot again.

        If nothing is specified, it unignores the current channel.

        To use this command you must have the Manage Server permission.
        """
        assert ctx.guild is not None

        if not entities:
            query = "DELETE FROM plonks WHERE guild_id=$1 AND entity_id=$2;"
            await ctx.db.execute(query, ctx.guild.id, ctx.channel.id)
        else:
            query = "DELETE FROM plonks WHERE guild_id=$1 AND entity_id = ANY($2::bigint[]);"
            new_entities = [c.id for c in entities]
            await ctx.db.execute(query, ctx.guild.id, new_entities)

        self.is_plonked.invalidate_containing(f"{ctx.guild.id!r}:")
        await ctx.send(ctx.tick(True))

    @unignore.command(name="all")
    @checks.is_mod()
    async def unignore_all(self, ctx: Context) -> None:
        """An alias for ignore clear command."""
        await self.ignore_clear(ctx)

    @config.group(aliases=["guild"])
    @checks.is_mod()
    async def server(self, ctx: Context) -> None:
        """Handles the server-specific permissions."""

    @config.group()
    @checks.is_mod()
    async def channel(self, ctx: Context) -> None:
        """Handles the channel-specific permissions."""

    async def command_toggle(
        self,
        connection: asyncpg.Connection | asyncpg.Pool,
        guild_id: int,
        channel_id: int | None,
        name: str,
        *,
        whitelist: bool = True,
    ) -> None:
        """Toggles the passed command."""
        # clear the cache
        self.get_command_permissions.invalidate(self, guild_id)

        if channel_id is None:
            subcheck = "channel_id IS NULL"
            args = (guild_id, name)
        else:
            subcheck = "channel_id=$3"
            args = (guild_id, name, channel_id)

        async with connection.transaction():  # type: ignore # this exists but asyncpg sucks
            # delete the previous entry regardless of what it was
            query = f"DELETE FROM command_config WHERE guild_id=$1 AND name=$2 AND {subcheck};"

            # DELETE <num>
            await connection.execute(query, *args)

            query = "INSERT INTO command_config (guild_id, channel_id, name, whitelist) VALUES ($1, $2, $3, $4);"

            try:
                await connection.execute(query, guild_id, channel_id, name, whitelist)
            except asyncpg.UniqueViolationError:
                msg = "This command is already disabled." if not whitelist else "This command is already explicitly enabled."
                raise RuntimeError(msg)

    @channel.command(name="disable")
    async def channel_disable(self, ctx: Context, *, command: str = commands.param(converter=CommandName)) -> None:
        """Disables a command for this channel."""
        assert ctx.guild is not None

        try:
            await self.command_toggle(ctx.pool, ctx.guild.id, ctx.channel.id, command, whitelist=False)
        except RuntimeError as err:
            await ctx.send(str(err))
        else:
            await ctx.send("Command successfully disabled for this channel.")

    @channel.command(name="enable")
    async def channel_enable(self, ctx: Context, *, command: str = commands.param(converter=CommandName)) -> None:
        """Enables a command for this channel."""
        assert ctx.guild is not None

        try:
            await self.command_toggle(ctx.pool, ctx.guild.id, ctx.channel.id, command, whitelist=True)
        except RuntimeError as err:
            await ctx.send(str(err))
        else:
            await ctx.send("Command successfully enabled for this channel.")

    @server.command(name="disable")
    async def server_disable(self, ctx: Context, *, command: str = commands.param(converter=CommandName)) -> None:
        """Disables a command for this server."""
        assert ctx.guild is not None

        try:
            await self.command_toggle(ctx.pool, ctx.guild.id, None, command, whitelist=False)
        except RuntimeError as err:
            await ctx.send(str(err))
        else:
            await ctx.send("Command successfully disabled for this server")

    @server.command(name="enable")
    async def server_enable(self, ctx: Context, *, command: str = commands.param(converter=CommandName)) -> None:
        """Enables a command for this server."""
        assert ctx.guild is not None

        try:
            await self.command_toggle(ctx.pool, ctx.guild.id, None, command, whitelist=True)
        except RuntimeError as err:
            await ctx.send(str(err))
        else:
            await ctx.send("Command successfully enabled for this server.")

    @config.command(name="enable")
    @checks.is_mod()
    async def config_enable(
        self,
        ctx: Context,
        channel: discord.TextChannel | None,
        *,
        command: str = commands.param(converter=CommandName),
    ) -> None:
        """Enables a command the server or a channel."""
        assert ctx.guild is not None

        channel_id = channel.id if channel else None
        human_friendly = channel.mention if channel else "the server"
        try:
            await self.command_toggle(ctx.pool, ctx.guild.id, channel_id, command, whitelist=True)
        except RuntimeError as err:
            await ctx.send(str(err))
        else:
            await ctx.send(f"Command successfully enabled for {human_friendly}.")

    @config.command(name="disable")
    @checks.is_mod()
    async def config_disable(
        self,
        ctx: Context,
        channel: discord.TextChannel | None,
        *,
        command: str = commands.param(converter=CommandName),
    ) -> None:
        """Disables a command for the server or a channel."""
        assert ctx.guild is not None

        channel_id = channel.id if channel else None
        human_friendly = channel.mention if channel else "the server"
        try:
            await self.command_toggle(ctx.pool, ctx.guild.id, channel_id, command, whitelist=False)
        except RuntimeError as err:
            await ctx.send(str(err))
        else:
            await ctx.send(f"Command successfully disabled for {human_friendly}.")

    @config.command(name="disabled")
    @checks.is_mod()
    async def config_disabled(self, ctx: Context, *, channel: discord.TextChannel | None = None) -> None:
        """Shows the disabled commands for the channel given."""
        assert ctx.guild is not None
        assert isinstance(ctx.channel, discord.TextChannel)

        channel = channel or ctx.channel
        resolved = await self.get_command_permissions(ctx.guild.id)  # type: ignore # typing is gay with instance bindings
        disabled = resolved.get_blocked_commands(channel.id)

        if len(disabled) > 15:
            value = await self.bot.mb_client.create_paste(content="\n".join(disabled), filename="disabled_things.txt")
        else:
            value = "\n".join(disabled) or "None!"
        await ctx.send(f"In {channel.mention} the following commands are disabled:\n{value}")

    @config.group(name="global")
    @commands.is_owner()
    async def _global(self, ctx: Context):
        """Handles global bot configuration."""

    @_global.command(name="block")
    async def global_block(self, ctx: Context, object_id: int):
        """Blocks a user or guild globally."""
        await self.bot._blacklist_add(object_id)
        await ctx.send(ctx.tick(True))

    @_global.command(name="unblock")
    async def global_unblock(self, ctx: Context, object_id: int):
        """Unblocks a user or guild globally."""
        await self.bot._blacklist_remove(object_id)
        await ctx.send(ctx.tick(True))


async def setup(bot: Mipha):
    await bot.add_cog(Config(bot))
