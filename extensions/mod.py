"""
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at http://mozilla.org/MPL/2.0/.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import enum
import io
import logging
import re
import shlex
from collections import Counter, defaultdict
from collections.abc import Callable
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.abc import Snowflake
from discord.ext import commands, tasks

from utilities import cache, checks, time
from utilities.context import Context
from utilities.converters import (
    DatetimeConverter,  # type: ignore # it is but annotation hacks
)
from utilities.converters import (
    WhenAndWhatConverter,  # type: ignore # it is but annotation hacks
)
from utilities.formats import format_dt, plural


if TYPE_CHECKING:
    from bot import Kukiko
    from extensions.reminders import Reminder

log = logging.getLogger(__name__)


class Arguments(argparse.ArgumentParser):
    def error(self, message):
        raise RuntimeError(message)


class RaidMode(enum.Enum):
    off = 0
    on = 1
    strict = 2

    def __str__(self):
        return str(self.name)


class MemberNotFound(Exception):
    pass


class ModConfig:
    __slots__ = (
        "raid_mode",
        "id",
        "bot",
        "broadcast_channel_id",
        "mention_count",
        "safe_mention_channel_ids",
    )

    @classmethod
    async def from_record(cls, record, bot: Kukiko):
        self = cls()

        # the basic configuration
        self.bot = bot
        self.raid_mode = record["raid_mode"]
        self.id = record["id"]
        self.broadcast_channel_id = record["broadcast_channel"]
        self.mention_count = record["mention_count"]
        self.safe_mention_channel_ids = set(record["safe_mention_channel_ids"] or [])
        return self

    @property
    def broadcast_channel(self):
        guild = self.bot.get_guild(self.id)
        return guild and guild.get_channel(self.broadcast_channel_id)


def can_execute_action(ctx: Context, user: discord.Member, target: discord.Member):
    assert ctx.guild is not None
    return user.id == ctx.bot.owner_id or user == ctx.guild.owner or user.top_role > target.top_role


async def resolve_member(guild: discord.Guild, member_id: int) -> discord.Member:
    member = guild.get_member(member_id)
    if member is None:
        if guild.chunked:
            raise MemberNotFound()
        try:
            member = await guild.fetch_member(member_id)
        except discord.NotFound:
            raise MemberNotFound() from None
    return member


class _Hackban:
    def __init__(self, id: int) -> None:
        self.id = id

    def __str__(self) -> str:
        return f"Member ID {self.id}"


class MemberID(commands.Converter):
    async def convert(self, ctx: Context, argument: str) -> _Hackban | discord.Member:
        assert ctx.guild is not None
        assert isinstance(ctx.author, discord.Member)
        try:
            m = await commands.MemberConverter().convert(ctx, argument)
        except commands.BadArgument:
            try:
                member_id = int(argument, base=10)
                m = await resolve_member(ctx.guild, member_id)
            except ValueError:
                raise commands.BadArgument(f"{argument} is not a valid member or member ID.") from None
            except MemberNotFound:
                # hackban case
                return _Hackban(member_id)  # type: ignore

        if not can_execute_action(ctx, ctx.author, m):
            raise commands.BadArgument("You cannot do this action on this user due to role hierarchy.")
        return m


class BannedMember(commands.Converter):
    async def convert(self, ctx: Context, argument: str) -> discord.guild.BanEntry:
        assert ctx.guild is not None
        ban_list = [ban async for ban in ctx.guild.bans()]
        try:
            member_id = int(argument, base=10)
            entity = discord.utils.find(lambda u: u.user.id == member_id, ban_list)
        except ValueError:
            entity = discord.utils.find(lambda u: str(u.user) == argument, ban_list)

        if entity is None:
            raise commands.BadArgument("Not a valid previously-banned member.")
        return entity


class ActionReason(commands.Converter):
    async def convert(self, ctx: Context, argument: str) -> str:
        ret = f"{ctx.author} (ID: {ctx.author.id}): {argument}"

        if len(ret) > 512:
            reason_max = 512 - len(ret) + len(argument)
            raise commands.BadArgument(f"Reason is too long ({len(argument)}/{reason_max})")
        return ret


def safe_reason_append(base: str, to_append: str) -> str:
    appended = base + f"({to_append})"
    if len(appended) > 512:
        return base
    return appended


class CooldownByContent(commands.CooldownMapping):
    def _bucket_key(self, message) -> tuple[int, str]:
        return (message.channel.id, message.content)


class SpamChecker:
    """This spam checker does a few things.

    1) It checks if a user has spammed more than 10 times in 12 seconds
    2) It checks if the content has been spammed 15 times in 17 seconds.
    3) It checks if new users have spammed 30 times in 35 seconds.
    4) It checks if "fast joiners" have spammed 10 times in 12 seconds.

    The second case is meant to catch alternating spam bots while the first one
    just catches regular singular spam bots.

    From experience these values aren't reached unless someone is actively spamming.
    """

    def __init__(self) -> None:
        self.by_content = CooldownByContent.from_cooldown(15, 17.0, commands.BucketType.member)
        self.by_user = commands.CooldownMapping.from_cooldown(10, 12.0, commands.BucketType.user)
        self.last_join = None
        self.new_user = commands.CooldownMapping.from_cooldown(30, 35.0, commands.BucketType.channel)

        self.fast_joiners = cache.ExpiringCache(seconds=1800.0)
        self.hit_and_run = commands.CooldownMapping.from_cooldown(10, 12, commands.BucketType.channel)

    def is_new(self, member: discord.Member) -> bool:
        assert member.joined_at is not None
        now = datetime.datetime.now(datetime.timezone.utc)
        seven_days_ago = now - datetime.timedelta(days=7)
        ninety_days_ago = now - datetime.timedelta(days=90)
        return member.created_at > ninety_days_ago or member.joined_at > seven_days_ago

    def is_spamming(self, message: discord.Message) -> bool:
        if message.guild is None:
            return False

        assert isinstance(message.author, discord.Member)

        current = message.created_at.replace(tzinfo=datetime.timezone.utc).timestamp()

        if message.author.id in self.fast_joiners:
            bucket = self.hit_and_run.get_bucket(message)
            if bucket.update_rate_limit(current):
                return True

        if self.is_new(message.author):
            new_bucket = self.new_user.get_bucket(message)
            if new_bucket.update_rate_limit(current):
                return True

        user_bucket = self.by_user.get_bucket(message)
        if user_bucket.update_rate_limit(current):
            return True

        content_bucket = self.by_content.get_bucket(message)
        if content_bucket.update_rate_limit(current):
            return True

        return False

    def is_fast_join(self, member):
        joined = member.joined_at or (datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc))
        if self.last_join is None:
            self.last_join = joined
            return False
        is_fast = (joined - self.last_join).total_seconds() <= 2.0
        self.last_join = joined
        if is_fast:
            self.fast_joiners[member.id] = True
        return is_fast


def can_mute():
    async def predicate(ctx: Context) -> bool:
        assert isinstance(ctx.author, discord.Member)
        is_owner = await ctx.bot.is_owner(ctx.author)
        if ctx.guild is None:
            return False

        if not ctx.author.guild_permissions.moderate_members and not is_owner:
            return False

        return True

    return commands.check(predicate)


class Mod(commands.Cog):
    """Moderation related commands."""

    def __init__(self, bot: Kukiko) -> None:
        self.bot = bot

        # guild_id: SpamChecker
        self._spam_check = defaultdict(SpamChecker)

        # guild_id: List[(member_id, insertion)]
        # A batch of data for bulk inserting mute role changes
        # True - insert, False - remove
        self._data_batch = defaultdict(list)
        self._batch_lock = asyncio.Lock()
        self._disable_lock = asyncio.Lock()

        # (guild_id, channel_id): List[str]
        # A batch list of message content for message
        self.message_batches = defaultdict(list)
        self._batch_message_lock = asyncio.Lock()
        self.bulk_send_messages.start()

        self._recently_blocked = set()

    def __repr__(self):
        return "<cogs.Mod>"

    def cog_unload(self):
        self.bulk_send_messages.stop()

    async def cog_command_error(self, ctx: Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.BadArgument):
            await ctx.send(str(error))
        elif isinstance(error, commands.CommandInvokeError):
            original = error.original
            if isinstance(original, discord.Forbidden):
                await ctx.send("I do not have permission to execute this action.")
            elif isinstance(original, discord.NotFound):
                await ctx.send(f"This entity does not exist: {original.text}")
            elif isinstance(original, discord.HTTPException):
                await ctx.send("Somehow, an unexpected error occurred. Try again later?")

    @tasks.loop(seconds=10.0)
    async def bulk_send_messages(self):
        async with self._batch_message_lock:
            for ((guild_id, channel_id), messages) in self.message_batches.items():
                guild = self.bot.get_guild(guild_id)
                channel = guild and guild.get_channel(channel_id)
                if channel is None:
                    continue
                assert isinstance(channel, discord.TextChannel)
                paginator = commands.Paginator(suffix="", prefix="")
                for message in messages:
                    paginator.add_line(message)

                for page in paginator.pages:
                    try:
                        await channel.send(page)
                    except discord.HTTPException:
                        pass

    @cache.cache()
    async def get_guild_config(self, guild_id):
        query = """SELECT * FROM guild_mod_config WHERE id=$1;"""
        async with self.bot.pool.acquire() as con:
            record = await con.fetchrow(query, guild_id)
            if record is not None:
                return await ModConfig.from_record(record, self.bot)
            return None

    async def check_raid(self, config, guild_id, member, message):
        if config.raid_mode != RaidMode.strict.value:
            return

        checker = self._spam_check[guild_id]
        if not checker.is_spamming(message):
            return

        try:
            await member.ban(reason="Auto-ban from spam (strict raid mode ban)")
        except discord.HTTPException:
            log.info(f"[Raid Mode] Failed to ban {member} (ID: {member.id}) from server {member.guild} via strict mode.")
        else:
            log.info(f"[Raid Mode] Banned {member} (ID: {member.id}) from server {member.guild} via strict mode.")

    @commands.Cog.listener()
    async def on_message(self, message):
        assert self.bot.user is not None

        author = message.author
        if author.id in (self.bot.user.id, self.bot.owner_id):
            return

        if message.guild is None:
            return

        if not isinstance(author, discord.Member):
            return

        if author.bot:
            return

        # we're going to ignore members with guild level manage messages.
        if message.channel.permissions_for(message.author).manage_messages:
            return

        guild_id = message.guild.id
        config = await self.get_guild_config(guild_id)  # type: ignore # typing is gay with instance bindings
        if config is None:
            return

        # check for raid mode stuff
        await self.check_raid(config, guild_id, author, message)

        # auto-ban tracking for mention spams begin here
        if len(message.mentions) <= 3:
            return

        if not config.mention_count:
            return

        # check if it meets the thresholds required
        mention_count = sum(not m.bot and m.id != author.id for m in message.mentions)
        if mention_count < config.mention_count:
            return

        if message.channel.id in config.safe_mention_channel_ids:
            return

        try:
            await author.ban(reason=f"Spamming mentions ({mention_count} mentions)")
        except Exception:
            log.info(f"Failed to autoban member {author} (ID: {author.id}) in guild ID {guild_id}")
        else:
            to_send = f"Banned {author} (ID: {author.id}) for spamming {mention_count} mentions."
            async with self._batch_message_lock:
                self.message_batches[(guild_id, message.channel_id)].append(to_send)

            log.info(f"Member {author} (ID: {author.id}) has been autobanned from guild ID {guild_id}")

    @commands.Cog.listener()
    async def on_member_join(self, member):
        guild_id = member.guild.id
        config = await self.get_guild_config(guild_id)  # type: ignore # typing is gay with instance bindings
        if config is None:
            return

        if not config.raid_mode:
            return

        now = datetime.datetime.now(datetime.timezone.utc)

        is_new = member.created_at > (now - datetime.timedelta(days=7))
        checker = self._spam_check[guild_id]

        # Do the broadcasted message to the channel
        title = "Member Joined"
        if checker.is_fast_join(member):
            colour = 0xDD5F53  # red
            if is_new:
                title = "Member Joined (Very New Member)"
        else:
            colour = 0x53DDA4  # green

            if is_new:
                colour = 0xDDA453  # yellow
                title = "Member Joined (Very New Member)"

        e = discord.Embed(title=title, colour=colour)
        e.timestamp = now
        e.set_author(name=str(member), icon_url=member.avatar.url)
        e.add_field(name="ID", value=member.id)
        e.add_field(name="Joined", value=format_dt(member.joined_at, "F"))
        e.add_field(name="Created", value=time.format_relative(member.created_at), inline=False)

        if config.broadcast_channel:
            try:
                await config.broadcast_channel.send(embed=e)
            except discord.Forbidden:
                async with self._disable_lock:
                    await self.disable_raid_mode(guild_id)

    @commands.command(aliases=["newmembers"])
    @commands.guild_only()
    async def newusers(self, ctx: Context, *, count: int = 5) -> None:
        """Tells you the newest members of the server.

        This is useful to check if any suspicious members have
        joined.

        The count parameter can only be up to 25.
        """
        assert ctx.guild is not None

        count = max(min(count, 25), 5)

        members = sorted(ctx.guild.members, key=lambda m: m.joined_at, reverse=True)[:count]  # type: ignore # thanks discord, joined_at being None is a meme

        e = discord.Embed(title="New Members", colour=discord.Colour.green())

        for member in members:
            body = f"Joined {time.format_relative(member.joined_at)}\nCreated {time.format_relative(member.created_at)}"
            e.add_field(name=f"{member} (ID: {member.id})", value=body, inline=False)

        await ctx.send(embed=e)

    def _hoisters_magic(self, guild: discord.Guild) -> discord.File | None:
        fmt = []
        for member in guild.members:
            character = ord(member.display_name[0])
            if (character < 65) or (90 < character < 97):
                fmt.append(member)

        if not fmt:
            return
        formatted = "\n".join(f"{member.name} || {member.display_name} ({member.id})" for member in fmt)

        out = io.BytesIO(formatted.encode())

        return discord.File(out, filename="hoisters.txt", spoiler=False)

    @commands.command(name="hoisters")
    @commands.cooldown(1, 15, commands.BucketType.user)
    async def hoister_message(self, ctx: Context, guild: discord.Guild | None = None) -> None:
        """
        Sends the author a list of members who are currently hosting in the member list.

        This is currently any punctuation character.
        """

        if guild is not None:
            owner = await ctx.bot.is_owner(ctx.author)
            if owner is False:
                guild = ctx.guild
        else:
            guild = ctx.guild

        if guild is None:
            raise commands.BadArgument("Please be in a guild when running this.")

        file = self._hoisters_magic(guild)
        if file is None:
            await ctx.send("No hoisters here!")
            return

        try:
            await ctx.author.send(file=file)
        except discord.Forbidden:
            await ctx.send("I couldn't DM you so here it is...", file=file)

    @app_commands.command()
    async def hoisters(self, interaction: discord.Interaction) -> None:
        """Will send a file containing all hoisters in the guild, with their 'Name: Display Name :: (ID)'"""
        if interaction.guild is None:
            await interaction.response.send_message("Can't do this in DMs!", ephemeral=True)
            return

        file = self._hoisters_magic(interaction.guild)
        if file is None:
            await interaction.response.send_message("No hoisters here!", ephemeral=True)
            return

        await interaction.response.send_message(file=file, ephemeral=True)

    @commands.group(aliases=["raids"], invoke_without_command=True)
    @checks.is_mod()
    async def raid(self, ctx: Context) -> None:
        """Controls raid mode on the server.

        Calling this command with no arguments will show the current raid
        mode information.

        You must have Manage Server permissions to use this command or
        its subcommands.
        """
        assert ctx.guild is not None

        query = "SELECT raid_mode, broadcast_channel FROM guild_mod_config WHERE id=$1;"

        row = await ctx.db.fetchrow(query, ctx.guild.id)
        if row is None:
            fmt = "Raid Mode: off\nBroadcast Channel: None"
        else:
            ch = f"<#{row[1]}>" if row[1] else None
            mode = RaidMode(row[0]) if row[0] is not None else RaidMode.off
            fmt = f"Raid Mode: {mode}\nBroadcast Channel: {ch}"

        await ctx.send(fmt)

    @raid.command(name="on", aliases=["enable", "enabled"])
    @checks.is_mod()
    async def raid_on(self, ctx: Context, *, channel: discord.TextChannel | None = None):
        """Enables basic raid mode on the server.

        When enabled, server verification level is set to table flip
        levels and allows the bot to broadcast new members joining
        to a specified channel.

        If no channel is given, then the bot will broadcast join
        messages on the channel this command was used in.
        """

        target_channel = channel or ctx.channel
        assert target_channel is not None
        assert isinstance(target_channel, discord.TextChannel)
        assert ctx.guild is not None

        try:
            await ctx.guild.edit(verification_level=discord.VerificationLevel.high)
        except discord.HTTPException:
            await ctx.send("\N{WARNING SIGN} Could not set verification level.")

        query = """INSERT INTO guild_mod_config (id, raid_mode, broadcast_channel)
                   VALUES ($1, $2, $3) ON CONFLICT (id)
                   DO UPDATE SET
                        raid_mode = EXCLUDED.raid_mode,
                        broadcast_channel = EXCLUDED.broadcast_channel;
                """

        await ctx.db.execute(query, ctx.guild.id, RaidMode.on.value, target_channel.id)
        self.get_guild_config.invalidate(self, ctx.guild.id)
        await ctx.send(f"Raid mode enabled. Broadcasting join messages to {target_channel.mention}.")

    async def disable_raid_mode(self, guild_id):
        query = """INSERT INTO guild_mod_config (id, raid_mode, broadcast_channel)
                   VALUES ($1, $2, NULL) ON CONFLICT (id)
                   DO UPDATE SET
                        raid_mode = EXCLUDED.raid_mode,
                        broadcast_channel = NULL;
                """

        await self.bot.pool.execute(query, guild_id, RaidMode.off.value)
        self._spam_check.pop(guild_id, None)
        self.get_guild_config.invalidate(self, guild_id)

    @raid.command(name="off", aliases=["disable", "disabled"])
    @checks.is_mod()
    async def raid_off(self, ctx: Context):
        """Disables raid mode on the server.

        When disabled, the server verification levels are set
        back to Low levels and the bot will stop broadcasting
        join messages.
        """
        assert ctx.guild is not None

        try:
            await ctx.guild.edit(verification_level=discord.VerificationLevel.low)
        except discord.HTTPException:
            await ctx.send("\N{WARNING SIGN} Could not set verification level.")

        await self.disable_raid_mode(ctx.guild.id)
        await ctx.send("Raid mode disabled. No longer broadcasting join messages.")

    @raid.command(name="strict")
    @checks.is_mod()
    async def raid_strict(self, ctx: Context, *, channel: discord.TextChannel | None = None):
        """Enables strict raid mode on the server.

        Strict mode is similar to regular enabled raid mode, with the added
        benefit of auto-banning members that are spamming. The threshold for
        spamming depends on a per-content basis and also on a per-user basis
        of 15 messages per 17 seconds.

        If this is considered too strict, it is recommended to fall back to regular
        raid mode.
        """
        assert isinstance(ctx.channel, discord.TextChannel)
        assert ctx.guild is not None

        channel = channel or ctx.channel

        perms = ctx.guild.me.guild_permissions
        if not (perms.kick_members and perms.ban_members):
            return await ctx.send("\N{NO ENTRY SIGN} I do not have permissions to kick and ban members.")

        try:
            await ctx.guild.edit(verification_level=discord.VerificationLevel.high)
        except discord.HTTPException:
            await ctx.send("\N{WARNING SIGN} Could not set verification level.")

        query = """INSERT INTO guild_mod_config (id, raid_mode, broadcast_channel)
                   VALUES ($1, $2, $3) ON CONFLICT (id)
                   DO UPDATE SET
                        raid_mode = EXCLUDED.raid_mode,
                        broadcast_channel = EXCLUDED.broadcast_channel;
                """

        await ctx.db.execute(query, ctx.guild.id, RaidMode.strict.value, channel.id)
        self.get_guild_config.invalidate(self, ctx.guild.id)
        await ctx.send(f"Raid mode enabled strictly. Broadcasting join messages to {channel.mention}.")

    async def _basic_cleanup_strategy(self, ctx: Context, search: int) -> dict[str, int]:
        count = 0
        async for msg in ctx.history(limit=search, before=ctx.message):
            if msg.author == ctx.me:
                await msg.delete()
                count += 1
        return {"Bot": count}

    async def _regular_user_cleanup_strategy(self, ctx: Context, search: int) -> Counter[str]:
        assert ctx.guild is not None
        assert ctx.channel is not None
        assert not isinstance(ctx.channel, discord.DMChannel)

        prefixes = tuple(self.bot._get_guild_prefixes(ctx.guild))

        def check(m: discord.Message) -> bool:
            return (m.author == ctx.me or m.content.startswith(prefixes)) and not (m.mentions or m.role_mentions)

        deleted = await ctx.channel.purge(limit=search, check=check, before=ctx.message)
        return Counter(m.author.display_name for m in deleted)

    async def _complex_cleanup_strategy(self, ctx: Context, search: int) -> Counter:
        assert ctx.guild is not None
        assert ctx.channel is not None
        assert not isinstance(ctx.channel, discord.DMChannel)

        prefixes = tuple(self.bot._get_guild_prefixes(ctx.guild))

        def check(message: discord.Message):
            return message.author == ctx.me or message.content.startswith(prefixes)

        deleted = await ctx.channel.purge(limit=search, check=check, before=ctx.message)
        return Counter(m.author.display_name for m in deleted)

    @commands.command()
    async def cleanup(self, ctx: Context, search: int = 100):
        """Cleans up the bot's messages from the channel.

        If a search number is specified, it searches that many messages to delete.
        If the bot has Manage Messages permissions then it will try to delete
        messages that look like they invoked the bot as well.

        After the cleanup is completed, the bot will send you a message with
        which people got their messages deleted and their count. This is useful
        to see which users are spammers.

        Members with Manage Messages can search up to 1000 messages.
        Members without can search up to 25 messages.
        """
        strategy = self._basic_cleanup_strategy
        is_mod = ctx.channel.permissions_for(ctx.author).manage_messages  # type: ignore # yeah this works but I'd need to narrow based on guild<>dm channel.

        if ctx.channel.permissions_for(ctx.me).manage_messages:  # type: ignore # same comment as above
            if is_mod:
                strategy = self._complex_cleanup_strategy
            else:
                strategy = self._regular_user_cleanup_strategy
            await ctx.message.delete()

        if is_mod:
            search = min(max(2, search), 1000)
        else:
            search = min(max(2, search), 25)

        spammers = await strategy(ctx, search)
        deleted = sum(spammers.values())
        messages = [f'{deleted} message{" was" if deleted == 1 else "s were"} removed.']
        if deleted:
            messages.append("")
            spammers = sorted(spammers.items(), key=lambda t: t[1], reverse=True)
            messages.extend(f"- **{author}**: {count}" for author, count in spammers)

        await ctx.send("\n".join(messages), delete_after=10)

    @commands.guild_only()
    @checks.has_permissions(kick_members=True)
    @commands.command()
    async def kick(
        self,
        ctx: Context,
        member: discord.Member if TYPE_CHECKING else MemberID,
        *,
        reason: (str if TYPE_CHECKING else ActionReason) | None = None,
    ):
        """Kicks a member from the server.

        In order for this to work, the bot must have Kick Member permissions.

        To use this command you must have Kick Members permission.
        """
        assert ctx.guild is not None

        reason = reason or f"Action done by {ctx.author} (ID: {ctx.author.id})"

        await ctx.guild.kick(member, reason=reason)
        embed = discord.Embed(title="Moderation action: Kick", colour=discord.Colour.dark_red())
        embed.timestamp = datetime.datetime.utcnow()
        embed.add_field(name="Target", value=member.name)
        embed.set_footer(text=reason)
        await ctx.send(embed=embed)

    @commands.command()
    @commands.guild_only()
    @checks.has_permissions(ban_members=True)
    async def ban(
        self,
        ctx: Context,
        member: discord.Member if TYPE_CHECKING else MemberID,
        *,
        reason: (str if TYPE_CHECKING else ActionReason) | None = None,
    ):
        """Bans a member from the server.

        You can also ban from ID to ban regardless whether they're
        in the server or not.

        In order for this to work, the bot must have Ban Member permissions.

        To use this command you must have Ban Members permission.
        """
        assert ctx.guild is not None

        reason = reason or f"Action done by {ctx.author} (ID: {ctx.author.id})"

        if member.id == ctx.author.id:
            return await ctx.send("Can't ban yourself, idiot.")

        await ctx.guild.ban(member, reason=reason)
        embed = discord.Embed(title="Moderation action: Ban", colour=discord.Colour(0xFFFFFF))
        embed.timestamp = datetime.datetime.utcnow()
        embed.add_field(name="Target", value=member)
        embed.set_footer(text=reason)
        await ctx.send(embed=embed)

    @commands.command()
    @commands.guild_only()
    @checks.has_permissions(ban_members=True)
    async def multiban(
        self,
        ctx: Context,
        members: commands.Greedy[discord.Member if TYPE_CHECKING else MemberID],
        *,
        reason: (str if TYPE_CHECKING else ActionReason) | None = None,
    ):
        """Bans multiple members from the server.

        This only works through banning via ID.

        In order for this to work, the bot must have Ban Member permissions.

        To use this command you must have Ban Members permission.
        """
        assert ctx.guild is not None

        reason = reason or f"Action done by {ctx.author} (ID: {ctx.author.id})"

        total_members = len(members)
        if total_members == 0:
            return await ctx.send("Missing members to ban.")

        confirm = await ctx.prompt(
            f"This will ban **{plural(total_members):member}**. Are you sure?",
            reacquire=False,
        )
        if not confirm:
            return await ctx.send("Aborting.")

        failed = 0
        for member in members:
            try:
                await ctx.guild.ban(member, reason=reason)
            except discord.HTTPException:
                failed += 1

        confirmation = f"Banned {total_members - failed}/{total_members} members."

        embed = discord.Embed(title="Moderation action: Ban", colour=discord.Colour(0x000001))
        embed.timestamp = datetime.datetime.utcnow()
        embed.add_field(name="Target", value="\n".join([str(m) for m in members]))
        embed.description = confirmation
        embed.set_footer(text=reason)

        await ctx.send(embed=embed)

    @commands.command()
    @commands.guild_only()
    @checks.has_permissions(ban_members=True)
    async def massban(self, ctx: Context, *, arguments: str) -> None:
        """Mass bans multiple members from the server.
        This command has a powerful "command line" syntax. To use this command
        you and the bot must both have Ban Members permission. **Every option is optional.**
        Users are only banned **if and only if** all conditions are met.
        The following options are valid.
        `--channel` or `-c`: Channel to search for message history.
        `--reason` or `-r`: The reason for the ban.
        `--regex`: Regex that usernames must match.
        `--created`: Matches users whose accounts were created less than specified minutes ago.
        `--joined`: Matches users that joined less than specified minutes ago.
        `--joined-before`: Matches users who joined before the member ID given.
        `--joined-after`: Matches users who joined after the member ID given.
        `--no-avatar`: Matches users who have no avatar. (no arguments)
        `--no-roles`: Matches users that have no role. (no arguments)
        `--show`: Show members instead of banning them (no arguments).
        Message history filters (Requires `--channel`):
        `--contains`: A substring to search for in the message.
        `--starts`: A substring to search if the message starts with.
        `--ends`: A substring to search if the message ends with.
        `--match`: A regex to match the message content to.
        `--search`: How many messages to search. Default 100. Max 2000.
        `--after`: Messages must come after this message ID.
        `--before`: Messages must come before this message ID.
        `--files`: Checks if the message has attachments (no arguments).
        `--embeds`: Checks if the message has embeds (no arguments).
        """
        assert ctx.guild is not None

        # For some reason there are cases due to caching that ctx.author
        # can be a User even in a guild only context
        # Rather than trying to work out the kink with it
        # Just upgrade the member itself.
        if not isinstance(ctx.author, discord.Member):
            try:
                author = await ctx.guild.fetch_member(ctx.author.id)
            except discord.HTTPException:
                await ctx.send("Somehow, Discord does not seem to think you are in this server.")
                return
        else:
            author = ctx.author

        parser = Arguments(add_help=False, allow_abbrev=False)
        parser.add_argument("--channel", "-c")
        parser.add_argument("--reason", "-r")
        parser.add_argument("--search", type=int, default=100)
        parser.add_argument("--regex")
        parser.add_argument("--no-avatar", action="store_true")
        parser.add_argument("--no-roles", action="store_true")
        parser.add_argument("--created", type=int)
        parser.add_argument("--joined", type=int)
        parser.add_argument("--joined-before", type=int)
        parser.add_argument("--joined-after", type=int)
        parser.add_argument("--contains")
        parser.add_argument("--starts")
        parser.add_argument("--ends")
        parser.add_argument("--match")
        parser.add_argument("--show", action="store_true")
        parser.add_argument("--embeds", action="store_const", const=lambda m: len(m.embeds))
        parser.add_argument("--files", action="store_const", const=lambda m: len(m.attachments))
        parser.add_argument("--after", type=int)
        parser.add_argument("--before", type=int)

        try:
            args = parser.parse_args(shlex.split(arguments))
        except Exception as e:
            await ctx.send(str(e))
            return

        members = []

        if args.channel:
            channel = await commands.TextChannelConverter().convert(ctx, args.channel)
            before = args.before and discord.Object(id=args.before)
            after = args.after and discord.Object(id=args.after)
            predicates = []
            if args.contains:
                predicates.append(lambda m: args.contains in m.content)
            if args.starts:
                predicates.append(lambda m: m.content.startswith(args.starts))
            if args.ends:
                predicates.append(lambda m: m.content.endswith(args.ends))
            if args.match:
                try:
                    _match = re.compile(args.match)
                except re.error as e:
                    await ctx.send(f"Invalid regex passed to `--match`: {e}")
                    return
                else:
                    predicates.append(lambda m, x=_match: x.match(m.content))
            if args.embeds:
                predicates.append(args.embeds)
            if args.files:
                predicates.append(args.files)

            async for message in channel.history(limit=min(max(1, args.search), 2000), before=before, after=after):
                if all(p(message) for p in predicates):
                    members.append(message.author)
        else:
            if ctx.guild.chunked:
                members = ctx.guild.members
            else:
                async with ctx.typing():
                    await ctx.guild.chunk(cache=True)
                members = ctx.guild.members

        # member filters
        predicates = [
            lambda m: isinstance(m, discord.Member) and can_execute_action(ctx, author, m),  # Only if applicable
            lambda m: not m.bot,  # No bots
            lambda m: m.discriminator != "0000",  # No deleted users
        ]

        converter = commands.MemberConverter()

        if args.regex:
            try:
                _regex = re.compile(args.regex)
            except re.error as e:
                await ctx.send(f"Invalid regex passed to `--regex`: {e}")
                return
            else:
                predicates.append(lambda m, x=_regex: x.match(m.name))

        if args.no_avatar:
            predicates.append(lambda m: m.avatar is None)
        if args.no_roles:
            predicates.append(lambda m: len(getattr(m, "roles", [])) <= 1)

        now = datetime.datetime.utcnow()

        if args.created:

            def created(member, *, offset=None):
                offset = offset or (now - datetime.timedelta(minutes=args.created)).replace(tzinfo=datetime.timezone.utc)
                return member.created_at > offset

            predicates.append(created)  # type: ignore # this predicates list is so invariant it's just not worth it

        if args.joined:

            def joined(member, *, offset=None):
                offset = offset or (now - datetime.timedelta(minutes=args.joined)).replace(tzinfo=datetime.timezone.utc)
                if isinstance(member, discord.User):
                    # If the member is a user then they left already
                    return True
                return member.joined_at and member.joined_at > offset

            predicates.append(joined)  # type: ignore # this predicates list is so invariant it's just not worth it

        if args.joined_after:
            _joined_after_member = await converter.convert(ctx, str(args.joined_after))

            def joined_after(member, *, _other=_joined_after_member):
                return member.joined_at and _other.joined_at and member.joined_at > _other.joined_at

            predicates.append(joined_after)  # type: ignore # this predicates list is so invariant it's just not worth it

        if args.joined_before:
            _joined_before_member = await converter.convert(ctx, str(args.joined_before))

            def joined_before(member, *, _other=_joined_before_member):
                return member.joined_at and _other.joined_at and member.joined_at < _other.joined_at

            predicates.append(joined_before)  # type: ignore # this predicates list is so invariant it's just not worth it

        members = {m for m in members if all(p(m) for p in predicates)}
        if len(members) == 0:
            await ctx.send("No members found matching criteria.")
            return

        if args.show:
            members = sorted(members, key=lambda m: m.joined_at or now)
            fmt = "\n".join(f"{m.id}\tJoined: {m.joined_at}\tCreated: {m.created_at}\t{m}" for m in members)
            content = f"Current Time: {datetime.datetime.utcnow()}\nTotal members: {len(members)}\n{fmt}"
            file = discord.File(io.BytesIO(content.encode("utf-8")), filename="members.txt")
            await ctx.send(file=file)
            return

        if args.reason is None:
            await ctx.send("--reason flag is required.")
            return
        else:
            reason = await ActionReason().convert(ctx, args.reason)

        confirm = await ctx.prompt(f"This will ban **{plural(len(members)):member}**. Are you sure?")
        if not confirm:
            await ctx.send("Aborting.")
            return

        count = 0
        for member in members:
            try:
                await ctx.guild.ban(member, reason=reason)
            except discord.HTTPException:
                pass
            else:
                count += 1

        await ctx.send(f"Banned {count}/{len(members)}")

    @commands.command()
    @commands.guild_only()
    @checks.has_permissions(kick_members=True)
    async def softban(
        self,
        ctx: Context,
        member: discord.Member if TYPE_CHECKING else MemberID,
        *,
        reason: (str if TYPE_CHECKING else ActionReason) | None = None,
    ):
        """Soft bans a member from the server.

        A softban is basically banning the member from the server but
        then unbanning the member as well. This allows you to essentially
        kick the member while removing their messages.

        In order for this to work, the bot must have Ban Member permissions.

        To use this command you must have Kick Members permissions.
        """
        assert ctx.guild is not None

        reason = reason or f"Action done by {ctx.author} (ID: {ctx.author.id})"

        await ctx.guild.ban(member, reason=reason)
        await ctx.guild.unban(member, reason=reason)
        embed = discord.Embed(title="Moderation action: Softban", colour=discord.Colour.greyple())
        embed.timestamp = datetime.datetime.utcnow()
        embed.add_field(name="Target", value=member)
        embed.set_footer(text=reason)
        await ctx.send(embed=embed)

    @commands.command()
    @commands.guild_only()
    @checks.has_permissions(ban_members=True)
    async def unban(
        self,
        ctx: Context,
        member: discord.guild.BanEntry if TYPE_CHECKING else BannedMember,
        *,
        reason: (str if TYPE_CHECKING else ActionReason) | None = None,
    ):
        """Unbans a member from the server.

        You can pass either the ID of the banned member or the Name#Discrim
        combination of the member. Typically the ID is easiest to use.

        In order for this to work, the bot must have Ban Member permissions.

        To use this command you must have Ban Members permissions.
        """
        assert ctx.guild is not None

        reason = reason or f"Action done by {ctx.author} (ID: {ctx.author.id})"

        await ctx.guild.unban(member.user, reason=reason)
        embed = discord.Embed(title="Moderation action: Unban", colour=discord.Colour(0x000001))
        embed.timestamp = datetime.datetime.utcnow()
        embed.add_field(name="Target", value=member.user.name)
        if member.reason:
            embed.set_footer(text=reason)
        else:
            embed.set_footer(text=f"Unbanned {member.user} (ID: {member.user.id}).")
        await ctx.send(embed=embed)

    @commands.command()
    @commands.guild_only()
    @checks.has_permissions(ban_members=True)
    async def tempban(
        self,
        ctx,
        duration: datetime.datetime if TYPE_CHECKING else DatetimeConverter,
        member: discord.Member if TYPE_CHECKING else MemberID,
        *,
        reason: (str if TYPE_CHECKING else ActionReason) | None = None,
    ):
        """Temporarily bans a member for the specified duration.

        The duration can be a a short time form, e.g. 30d or a more human
        duration such as "until thursday at 3PM" or a more concrete time
        such as "2024-12-31".

        Note that times are in UTC.

        You can also ban from ID to ban regardless whether they're
        in the server or not.

        In order for this to work, the bot must have Ban Member permissions.

        To use this command you must have Ban Members permission.
        """

        reason = reason or f"Action done by {ctx.author} (ID: {ctx.author.id})"

        reminder: Reminder | None = self.bot.get_cog("Reminder")  # type: ignore # yeah idk
        if reminder is None:
            return await ctx.send("Sorry, this functionality is currently unavailable. Try again later?")

        until = f"until {format_dt(duration, 'F')}"

        reason = safe_reason_append(reason, until)
        await ctx.guild.ban(member, reason=reason)
        timer = await reminder.create_timer(
            duration,
            "tempban",
            ctx.guild.id,
            ctx.author.id,
            member.id,
            connection=ctx.db,
            created=ctx.message.created_at,
        )
        embed = discord.Embed(title="Moderation action: Temp Ban", colour=discord.Colour(0x000001))
        embed.timestamp = timer.created_at
        embed.add_field(name="Target", value=member.name)
        embed.description = reason
        embed.set_footer(text=f"Banned {member} for {time.human_timedelta(duration, source=timer.created_at)}.")
        await ctx.send(embed=embed)

    @commands.Cog.listener()
    async def on_tempban_timer_complete(self, timer):
        guild_id, mod_id, member_id = timer.args
        await self.bot.wait_until_ready()

        guild = self.bot.get_guild(guild_id)
        if guild is None:
            # RIP
            return

        moderator = guild.get_member(mod_id)
        if moderator is None:
            try:
                moderator = await self.bot.fetch_user(mod_id)
            except discord.HTTPException:
                # request failed somehow
                moderator = f"Mod ID {mod_id}"
            else:
                moderator = f"{moderator} (ID: {mod_id})"
        else:
            moderator = f"{moderator} (ID: {mod_id})"

        reason = f"Automatic unban from timer made on {timer.created_at} by {moderator}."
        await guild.unban(discord.Object(id=member_id), reason=reason)

    @commands.group(invoke_without_command=True)
    @commands.guild_only()
    @checks.has_permissions(ban_members=True)
    async def mentionspam(self, ctx: Context, count: int | None = None):
        """Enables auto-banning accounts that spam mentions.

        If a message contains `count` or more mentions then the
        bot will automatically attempt to auto-ban the member.
        The `count` must be greater than 3. If the `count` is 0
        then this is disabled.

        This only applies for user mentions. Everyone or Role
        mentions are not included.

        To use this command you must have the Ban Members permission.
        """
        assert ctx.guild is not None

        if count is None:
            query = """SELECT mention_count, COALESCE(safe_mention_channel_ids, '{}') AS channel_ids
                       FROM guild_mod_config
                       WHERE id=$1;
                    """

            row = await ctx.db.fetchrow(query, ctx.guild.id)
            if row is None or not row["mention_count"]:
                return await ctx.send("This server has not set up mention spam banning.")

            ignores = ", ".join(f"<#{e}>" for e in row["channel_ids"]) or "None"
            return await ctx.send(f'- Threshold: {row["mention_count"]} mentions\n- Ignored Channels: {ignores}')

        if count == 0:
            query = """UPDATE guild_mod_config SET mention_count = NULL WHERE id=$1;"""
            await ctx.db.execute(query, ctx.guild.id)
            self.get_guild_config.invalidate(self, ctx.guild.id)
            return await ctx.send("Auto-banning members has been disabled.")

        if count <= 3:
            await ctx.send("\N{NO ENTRY SIGN} Auto-ban threshold must be greater than three.")
            return

        query = """INSERT INTO guild_mod_config (id, mention_count, safe_mention_channel_ids)
                   VALUES ($1, $2, '{}')
                   ON CONFLICT (id) DO UPDATE SET
                       mention_count = $2;
                """
        await ctx.db.execute(query, ctx.guild.id, count)
        self.get_guild_config.invalidate(self, ctx.guild.id)
        await ctx.send(f"Now auto-banning members that mention more than {count} users.")

    @mentionspam.command(name="ignore", aliases=["bypass"])
    @commands.guild_only()
    @checks.has_permissions(ban_members=True)
    async def mentionspam_ignore(self, ctx: Context, *channels: discord.TextChannel):
        """Specifies what channels ignore mentionspam auto-bans.

        If a channel is given then that channel will no longer be protected
        by auto-banning from mention spammers.

        To use this command you must have the Ban Members permission.
        """
        assert ctx.guild is not None

        query = """UPDATE guild_mod_config
                   SET safe_mention_channel_ids =
                       ARRAY(SELECT DISTINCT * FROM unnest(COALESCE(safe_mention_channel_ids, '{}') || $2::bigint[]))
                   WHERE id = $1;
                """

        if len(channels) == 0:
            return await ctx.send("Missing channels to ignore.")

        channel_ids = [c.id for c in channels]
        await ctx.db.execute(query, ctx.guild.id, channel_ids)
        self.get_guild_config.invalidate(self, ctx.guild.id)
        await ctx.send(f'Mentions are now ignored on {", ".join(c.mention for c in channels)}.')

    @mentionspam.command(name="unignore", aliases=["protect"])
    @commands.guild_only()
    @checks.has_permissions(ban_members=True)
    async def mentionspam_unignore(self, ctx: Context, *channels: discord.TextChannel):
        """Specifies what channels to take off the ignore list.

        To use this command you must have the Ban Members permission.
        """
        assert ctx.guild is not None

        if len(channels) == 0:
            return await ctx.send("Missing channels to protect.")

        query = """UPDATE guild_mod_config
                   SET safe_mention_channel_ids =
                       ARRAY(SELECT element FROM unnest(safe_mention_channel_ids) AS element
                             WHERE NOT(element = ANY($2::bigint[])))
                   WHERE id = $1;
                """

        await ctx.db.execute(query, ctx.guild.id, [c.id for c in channels])
        self.get_guild_config.invalidate(self, ctx.guild.id)
        await ctx.send("Updated mentionspam ignore list.")

    @commands.group()
    @commands.guild_only()
    @checks.has_permissions(manage_messages=True)
    async def remove(self, ctx: Context) -> None:
        """Removes messages that meet a criteria.

        In order to use this command, you must have Manage Messages permissions.
        Note that the bot needs Manage Messages as well. These commands cannot
        be used in a private message.

        When the command is done doing its work, you will get a message
        detailing which users got removed and how many messages got removed.
        """

        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    async def do_removal(
        self,
        ctx: Context,
        limit: int,
        predicate: Callable[..., bool],
        *,
        before: Snowflake | None = None,
        after: Snowflake | None = None,
    ) -> None:
        if limit > 2000:
            await ctx.send(f"Too many messages to search given ({limit}/2000)")
            return

        if before is None:
            before = ctx.message
        else:
            before = discord.Object(id=before.id)

        if after is not None:
            after = discord.Object(id=after.id)

        assert isinstance(ctx.channel, discord.TextChannel)
        try:
            deleted = await ctx.channel.purge(limit=limit, before=before, after=after, check=predicate)
        except discord.Forbidden:
            await ctx.send("I do not have permissions to delete messages.")
            return
        except discord.HTTPException as e:
            await ctx.send(f"Error: {e} (try a smaller search?)")
            return

        spammers = Counter(m.author.display_name for m in deleted)
        deleted = len(deleted)
        messages = [f'{deleted} message{" was" if deleted == 1 else "s were"} removed.']
        if deleted:
            messages.append("")
            spammers = sorted(spammers.items(), key=lambda t: t[1], reverse=True)
            messages.extend(f"**{name}**: {count}" for name, count in spammers)

        to_send = "\n".join(messages)

        if len(to_send) > 2000:
            await ctx.send(f"Successfully removed {deleted} messages.", delete_after=10)
        else:
            await ctx.send(to_send, delete_after=10)

    @remove.command()
    async def embeds(self, ctx: Context, search: int = 100) -> None:
        """Removes messages that have embeds in them."""
        await self.do_removal(ctx, search, lambda e: len(e.embeds))  # type: ignore # this predicates list is so invariant it's just not worth it

    @remove.command()
    async def files(self, ctx: Context, search: int = 100) -> None:
        """Removes messages that have attachments in them."""
        await self.do_removal(ctx, search, lambda e: len(e.attachments))  # type: ignore # this predicates list is so invariant it's just not worth it

    @remove.command()
    async def images(self, ctx: Context, search: int = 100) -> None:
        """Removes messages that have embeds or attachments."""
        await self.do_removal(ctx, search, lambda e: len(e.embeds) or len(e.attachments))  # type: ignore # this predicates list is so invariant it's just not worth it

    @remove.command(name="all")
    async def _remove_all(self, ctx: Context, search: int = 100) -> None:
        """Removes all messages."""
        await self.do_removal(ctx, search, lambda e: True)

    @remove.command()
    async def user(self, ctx: Context, member: discord.Member, search=100) -> None:
        """Removes all messages by the member."""
        await self.do_removal(ctx, search, lambda e: e.author == member)

    @remove.command()
    async def contains(self, ctx: Context, *, substr: str) -> None:
        """Removes all messages containing a substring.

        The substring must be at least 3 characters long.
        """
        if len(substr) < 3:
            await ctx.send("The substring length must be at least 3 characters.")
        else:
            await self.do_removal(ctx, 100, lambda e: substr in e.content)

    @remove.command(name="bot", aliases=["bots"])
    async def _bot(self, ctx: Context, prefix=None, search=100) -> None:
        """Removes a bot user's messages and messages with their optional prefix."""

        def predicate(m):
            return (m.webhook_id is None and m.author.bot) or (prefix and m.content.startswith(prefix))

        await self.do_removal(ctx, search, predicate)

    @remove.command(name="webhook")
    async def _webhook(self, ctx: Context, search=100) -> None:
        """Removes a bot user's messages and messages with their optional prefix."""

        def predicate(m):
            return m.webhook_id is not None and m.author.bot

        await self.do_removal(ctx, search, predicate)

    @remove.command(name="emoji", aliases=["emojis"])
    async def _emoji(self, ctx: Context, search=100) -> None:
        """Removes all messages containing custom emoji."""
        custom_emoji = re.compile(r"<a?:[a-zA-Z0-9\_]+:([0-9]+)>")

        def predicate(m):
            return custom_emoji.search(m.content)

        await self.do_removal(ctx, search, predicate)  # type: ignore # this predicates list is so invariant it's just not worth it

    @remove.command(name="reactions")
    async def _reactions(self, ctx: Context, search: int = 100) -> None:
        """Removes all reactions from messages that have them."""

        if search > 2000:
            await ctx.send(f"Too many messages to search for ({search}/2000)")
            return

        total_reactions = 0
        async for message in ctx.history(limit=search, before=ctx.message):
            if len(message.reactions):
                total_reactions += sum(r.count for r in message.reactions)
                await message.clear_reactions()

        await ctx.send(f"Successfully removed {total_reactions} reactions.")

    @remove.command()
    async def custom(self, ctx: Context, *, arguments: str) -> None:
        """A more advanced purge command.

        This command uses a powerful "command line" syntax.
        Most options support multiple values to indicate 'any' match.
        If the value has spaces it must be quoted.

        The messages are only deleted if all options are met unless
        the `--or` flag is passed, in which case only if any is met.

        The following options are valid.

        `--user`: A mention or name of the user to remove.
        `--contains`: A substring to search for in the message.
        `--starts`: A substring to search if the message starts with.
        `--ends`: A substring to search if the message ends with.
        `--search`: How many messages to search. Default 100. Max 2000.
        `--after`: Messages must come after this message ID.
        `--before`: Messages must come before this message ID.

        Flag options (no arguments):

        `--bot`: Check if it's a bot user.
        `--embeds`: Check if the message has embeds.
        `--files`: Check if the message has attachments.
        `--emoji`: Check if the message has custom emoji.
        `--reactions`: Check if the message has reactions
        `--or`: Use logical OR for all options.
        `--not`: Use logical NOT for all options.
        """
        parser = Arguments(add_help=False, allow_abbrev=False)
        parser.add_argument("--user", nargs="+")
        parser.add_argument("--contains", nargs="+")
        parser.add_argument("--starts", nargs="+")
        parser.add_argument("--ends", nargs="+")
        parser.add_argument("--or", action="store_true", dest="_or")
        parser.add_argument("--not", action="store_true", dest="_not")
        parser.add_argument("--emoji", action="store_true")
        parser.add_argument("--bot", action="store_const", const=lambda m: m.author.bot)
        parser.add_argument("--embeds", action="store_const", const=lambda m: len(m.embeds))
        parser.add_argument("--files", action="store_const", const=lambda m: len(m.attachments))
        parser.add_argument("--reactions", action="store_const", const=lambda m: len(m.reactions))
        parser.add_argument("--search", type=int)
        parser.add_argument("--after", type=int)
        parser.add_argument("--before", type=int)

        try:
            args = parser.parse_args(shlex.split(arguments))
        except Exception as e:
            await ctx.send(str(e))
            return

        predicates = []
        if args.bot:
            predicates.append(args.bot)

        if args.embeds:
            predicates.append(args.embeds)

        if args.files:
            predicates.append(args.files)

        if args.reactions:
            predicates.append(args.reactions)

        if args.emoji:
            custom_emoji = re.compile(r"<:(\w+):(\d+)>")
            predicates.append(lambda m: custom_emoji.search(m.content))

        if args.user:
            users = []
            converter = commands.MemberConverter()
            for u in args.user:
                try:
                    user = await converter.convert(ctx, u)
                    users.append(user)
                except Exception as e:
                    await ctx.send(str(e))
                    return

            predicates.append(lambda m: m.author in users)

        if args.contains:
            predicates.append(lambda m: any(sub in m.content for sub in args.contains))

        if args.starts:
            predicates.append(lambda m: any(m.content.startswith(s) for s in args.starts))

        if args.ends:
            predicates.append(lambda m: any(m.content.endswith(s) for s in args.ends))

        op = all if not args._or else any

        def predicate(m: discord.Message) -> bool:
            r = op(p(m) for p in predicates)
            if args._not:
                return not r
            return r

        if args.after:
            if args.search is None:
                args.search = 2000

        if args.search is None:
            args.search = 100

        args.search = max(0, min(2000, args.search))  # clamp from 0-2000
        await self.do_removal(ctx, args.search, predicate, before=args.before, after=args.after)

    @commands.command(aliases=["timeout"])
    @can_mute()
    async def mute(
        self,
        ctx: Context,
        members: commands.Greedy[discord.Member],
        *,
        details: tuple[datetime.datetime, str] if TYPE_CHECKING else WhenAndWhatConverter,
    ):
        """Temporarily mutes members for the specified duration.

        Will consume as many members as possible before a time.

        The duration can be a a short time form, e.g. 30d or a more human
        duration such as "until thursday at 3PM" or a more concrete time
        such as "2024-12-31".

        Note that times are in UTC.
        """
        dt, reason = details

        reason = reason or f"Action done by {ctx.author} (ID: {ctx.author.id})"

        flag = False
        if dt > (ctx.message.created_at + datetime.timedelta(days=28)):
            flag = True
            dt = ctx.message.created_at + datetime.timedelta(days=28)

        failed = 0
        for member in members:
            try:
                await member.edit(timed_out_until=dt, reason=reason)
            except discord.HTTPException:
                failed += 1
        total = len(members) - failed // len(members)
        fmt = f"Muted {plural(total):member}, unmutes at: {discord.utils.format_dt(dt, 'F')}."
        if flag is True:
            fmt += "\nNote: The time was capped at 28 days from now."
        await ctx.send(fmt)

    @commands.command(name="unmute")
    @can_mute()
    async def _unmute(
        self,
        ctx: Context,
        members: commands.Greedy[discord.Member],
        *,
        reason: (str if TYPE_CHECKING else ActionReason) | None = None,
    ):
        """Unmutes members using the configured mute role.

        The bot must have Manage Roles permission and be
        above the muted role in the hierarchy.

        To use this command you need to be higher than the
        mute role in the hierarchy and have Manage Roles
        permission at the server level.
        """

        reason = reason or f"Action done by {ctx.author} (ID: {ctx.author.id})"

        total = len(members)
        if total == 0:
            return await ctx.send("Missing members to mute.")

        failed = 0
        for member in members:
            try:
                await member.edit(timed_out_until=None, reason=reason)
            except discord.HTTPException:
                failed += 1

        if failed == 0:
            await ctx.send("\N{THUMBS UP SIGN}")
        else:
            await ctx.send(f"Unmuted [{total - failed}/{total}]")

    @commands.command()
    @commands.guild_only()
    async def selfmute(self, ctx: Context, *, duration: time.ShortTime):
        """Temporarily mutes yourself for the specified duration.

        The duration must be in a short time form, e.g. 4h. Can
        only mute yourself for a maximum of 24 hours and a minimum
        of 5 minutes.
        (This has a maximum clamp of 24 hours).

        Do not ask a moderator to unmute you.
        """
        assert isinstance(ctx.author, discord.Member)
        assert ctx.guild is not None

        created_at = ctx.message.created_at
        if duration.dt > (created_at + datetime.timedelta(days=1)):
            duration.dt = created_at + datetime.timedelta(days=1)
            # return await ctx.send("Duration is too long. Must be at most 24 hours.")

        if duration.dt < (created_at + datetime.timedelta(minutes=5)):
            return await ctx.send("Duration is too short. Must be at least 5 minutes.")

        delta = discord.utils.format_dt(duration.dt, style="F")
        warning = f"Are you sure you want to be muted until {delta}?\n**Do not ask the moderators to undo this!**"
        confirm = await ctx.prompt(warning, reacquire=False)
        if not confirm:
            return await ctx.send("Aborting", delete_after=5.0)

        reason = f"Self-mute for {ctx.author} (ID: {ctx.author.id}) for {delta}"
        await ctx.author.edit(timed_out_until=duration.dt, reason=reason)

        await ctx.send(f"\N{OK HAND SIGN} Muted for {delta}. Be sure not to bother anyone about it.")

    @selfmute.error
    async def on_selfmute_error(self, ctx: Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("Missing a duration to selfmute for.")

    @commands.command(enabled=False)
    @commands.guild_only()
    @commands.bot_has_guild_permissions(ban_members=True)
    async def selfban(self, ctx: Context) -> None:
        """This is a totally destructive Ban. It won't be undone without begging moderators. By agreeing you agree you're gone forever."""
        assert isinstance(ctx.author, discord.Member)
        confirm = await ctx.prompt("This is a self **ban**. There is no undoing this.")
        if confirm:
            return await ctx.author.ban(reason="Suicide.", delete_message_days=0)


async def setup(bot: Kukiko) -> None:
    await bot.add_cog(Mod(bot))
