"""
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at http://mozilla.org/MPL/2.0/.
"""

from __future__ import annotations

import datetime
import inspect
import json
import os
import unicodedata
from collections import Counter
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from utilities import checks, formats, time
from utilities._types.discord_ import MessageableGuildChannel
from utilities.context import Context


if TYPE_CHECKING:
    from bot import Kukiko

GuildChannel = discord.TextChannel | discord.VoiceChannel | discord.StageChannel | discord.CategoryChannel | discord.Thread


class Prefix(commands.Converter):
    async def convert(self, ctx: Context, argument: str) -> str:
        assert ctx.bot.user is not None

        user_id = ctx.bot.user.id
        if argument.startswith((f"<@{user_id}>", f"<@!{user_id}>")):
            raise commands.BadArgument("That is a reserved prefix already in use.")
        return argument


class Meta(commands.Cog):
    """Commands for utilities related to Discord or the Bot itself."""

    def __init__(self, bot: Kukiko) -> None:
        self.bot = bot

    async def cog_command_error(self, ctx: Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.BadArgument):
            await ctx.send(str(error))

    @commands.command()
    async def ping(self, ctx: Context) -> None:
        """Ping commands are stupid."""
        await ctx.send("Ping commands are stupid.")

    @commands.command()
    async def charinfo(self, ctx: Context, *, characters: str) -> None:
        """Shows you information about a number of characters.

        Only up to 25 characters at a time.
        """

        def to_string(c):
            digit = f"{ord(c):x}"
            name = unicodedata.name(c, "Name not found.")
            return f"`\\U{digit:>08}`: {name} - {c} \N{EM DASH} <http://www.fileformat.info/info/unicode/char/{digit}>"

        msg = "\n".join(map(to_string, characters))
        await ctx.send(msg)

    @commands.group(name="prefix", invoke_without_command=True)
    async def prefix(self, ctx: Context) -> None:
        """Manages the server's custom prefixes.

        If called without a subcommand, this will list the currently set
        prefixes.
        """
        assert ctx.guild is not None

        prefixes = self.bot._get_guild_prefixes(ctx.guild)

        # we want to remove prefix #2, because it's the 2nd form of the mention
        # and to the end user, this would end up making them confused why the
        # mention is there twice
        del prefixes[1]

        e = discord.Embed(title="Prefixes", colour=discord.Colour.blurple())
        e.set_footer(text=f"{len(prefixes)} prefixes")
        e.description = "\n".join(f"{index}. {elem}" for index, elem in enumerate(prefixes, 1))
        await ctx.send(embed=e)

    @prefix.command(name="add", ignore_extra=False)
    @checks.is_mod()
    async def prefix_add(
        self,
        ctx: Context,
        prefix: str = commands.param(converter=Prefix),
    ) -> None:
        """Appends a prefix to the list of custom prefixes.

        Previously set prefixes are not overridden.

        To have a word prefix, you should quote it and end it with
        a space, e.g. "hello " to set the prefix to "hello ". This
        is because Discord removes spaces when sending messages so
        the spaces are not preserved.

        Multi-word prefixes must be quoted also.

        You must have Manage Server permission to use this command.
        """
        assert ctx.guild is not None

        current_prefixes = self.bot._get_guild_prefixes(ctx.guild, raw=True)
        current_prefixes.append(prefix)
        try:
            await self.bot._set_guild_prefixes(ctx.guild, current_prefixes)
        except Exception as e:
            await ctx.send(f"{ctx.tick(False)} {e}")
        else:
            await ctx.send(ctx.tick(True))

    @prefix_add.error
    async def prefix_add_error(self, ctx: Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.TooManyArguments):
            await ctx.send("You've given too many prefixes. Either quote it or only do it one by one.")

    @prefix.command(name="remove", aliases=["delete"], ignore_extra=False)
    @checks.is_mod()
    async def prefix_remove(
        self,
        ctx: Context,
        prefix: str = commands.param(converter=Prefix),
    ) -> None:
        """Removes a prefix from the list of custom prefixes.

        This is the inverse of the 'prefix add' command. You can
        use this to remove prefixes from the default set as well.

        You must have Manage Server permission to use this command.
        """
        assert ctx.guild is not None

        current_prefixes = self.bot._get_guild_prefixes(ctx.guild, raw=True)

        try:
            current_prefixes.remove(prefix)
        except ValueError:
            await ctx.send("I do not have this prefix registered.")
            return

        try:
            await self.bot._set_guild_prefixes(ctx.guild, current_prefixes)
        except Exception as e:
            await ctx.send(f"{ctx.tick(False)} {e}")
        else:
            await ctx.send(ctx.tick(True))

    @prefix.command(name="clear")
    @checks.is_mod()
    async def prefix_clear(self, ctx: Context) -> None:
        """Removes all custom prefixes.

        After this, the bot will listen to only mention prefixes.

        You must have Manage Server permission to use this command.
        """
        assert ctx.guild is not None

        await self.bot._set_guild_prefixes(ctx.guild, [])
        await ctx.send(ctx.tick(True))

    @commands.command()
    async def source(self, ctx: Context, *, command: str | None = None) -> None:
        """Displays my full source code or for a specific command.

        To display the source code of a subcommand you can separate it by
        periods, e.g. tag.create for the create subcommand of the tag command
        or by spaces.
        """
        source_url = "https://github.com/AbstractUmbra/Kukiko"
        branch = "main"
        if command is None:
            await ctx.send(source_url)
            return

        if command == "help":
            src = type(self.bot.help_command)
            module = src.__module__
            filename = inspect.getsourcefile(src)
            assert filename is not None  # this can't be None if the command is valid
        else:
            obj = self.bot.get_command(command.replace(".", " "))
            if obj is None:
                await ctx.send("Could not find command.")
                return

            # since we found the command we're looking for, presumably anyway, let's
            # try to access the code itself
            src = obj.callback.__code__
            module = obj.callback.__module__
            filename = src.co_filename

        lines, firstlineno = inspect.getsourcelines(src)
        if not module.startswith("discord"):
            # not a built-in command
            location = os.path.relpath(filename).replace("\\", "/")
        else:
            location = module.replace(".", "/") + ".py"
            source_url = "https://github.com/Rapptz/discord.py"
            branch = "master"

        final_url = f"<{source_url}/blob/{branch}/{location}#L{firstlineno}-L{firstlineno + len(lines) - 1}>"
        await ctx.send(final_url)

    @commands.command()
    async def avatar(self, ctx: Context, *, user: discord.Member | discord.User | None = None) -> None:
        """Shows a user's enlarged avatar(if possible)."""

        embed = discord.Embed()
        user = user or ctx.author
        avatar = user.display_avatar.with_static_format("png")
        embed.set_author(name=str(user), url=avatar)
        embed.set_image(url=avatar)
        await ctx.send(embed=embed)

    @commands.command(aliases=["userinfo"])
    async def info(self, ctx: Context, *, user: discord.Member | discord.User | None = None) -> None:
        """Shows info about a user."""

        user = user or ctx.author
        if ctx.guild and isinstance(user, discord.User):
            user = ctx.guild.get_member(user.id) or user

        e = discord.Embed()
        roles = [role.mention for role in user.roles[1:]] if isinstance(user, discord.Member) else ["N/A"]
        shared = sum(g.get_member(user.id) is not None for g in self.bot.guilds)
        e.set_author(name=str(user))

        def format_date(dt: datetime.datetime | None) -> str:
            if dt is None:
                return "N/A"
            return f"{dt:%Y-%m-%d %H:%M} ({discord.utils.format_dt(dt, 'R')})"

        e.add_field(name="ID", value=user.id, inline=False)
        e.add_field(name="Servers", value=f"{shared} shared", inline=False)
        e.add_field(
            name="Joined",
            value=format_date(getattr(user, "joined_at", None)),
            inline=False,
        )
        e.add_field(name="Created", value=format_date(user.created_at), inline=False)

        voice = getattr(user, "voice", None)
        if voice is not None:
            vc = voice.channel
            other_people = len(vc.members) - 1
            voice = f"{vc.name} with {other_people} others" if other_people else f"{vc.name} by themselves"
            e.add_field(name="Voice", value=voice, inline=False)

        if roles:
            e.add_field(
                name="Roles",
                value=", ".join(roles) if len(roles) < 10 else f"{len(roles)} roles",
                inline=False,
            )

        colour = user.colour
        if colour.value:
            e.colour = colour

        if user.avatar:
            e.set_thumbnail(url=user.avatar.url)

        if isinstance(user, discord.User):
            e.set_footer(text="This member is not in this server.")

        await ctx.send(embed=e)

    @commands.command(aliases=["guildinfo"], usage="")
    @commands.guild_only()
    async def serverinfo(self, ctx: Context, *, guild: discord.Guild | None = None) -> None:
        """Shows info about the current server."""

        if await self.bot.is_owner(ctx.author):
            guild = guild or ctx.guild
        else:
            guild = ctx.guild

        assert guild is not None

        roles = [role.mention for role in guild.roles[1:]]
        roles = roles or ["No extra roles"]

        # figure out what channels are 'secret'
        everyone = guild.default_role
        everyone_perms = everyone.permissions.value
        secret = Counter()
        totals = Counter()
        for channel in guild.channels:
            allow, deny = channel.overwrites_for(everyone).pair()
            perms = discord.Permissions((everyone_perms & ~deny.value) | allow.value)
            channel_type = type(channel)
            totals[channel_type] += 1
            if not perms.read_messages:
                secret[channel_type] += 1
            elif isinstance(channel, discord.VoiceChannel) and (not perms.connect or not perms.speak):
                secret[channel_type] += 1

        member_by_status = Counter(str(m.status) for m in guild.members)

        e = discord.Embed()
        e.title = guild.name
        e.description = f"**ID**: {guild.id}\n**Owner**: {guild.owner}"
        if guild.icon:
            e.set_thumbnail(url=guild.icon.url)

        channel_info = []
        key_to_emoji = {
            discord.TextChannel: "<:TextChannel:745076999160070296>",
            discord.VoiceChannel: "<:VoiceChannel:745077018080575580>",
        }
        for key, total in totals.items():
            secrets = secret[key]
            try:
                emoji = key_to_emoji[key]
            except KeyError:
                continue

            if secrets:
                channel_info.append(f"{emoji} {total} ({secrets} locked)")
            else:
                channel_info.append(f"{emoji} {total}")

        info = []
        features = set(guild.features)

        for feature in features:
            info.append(f"{ctx.tick(True)}: {feature.replace('_', ' ').title()}")

        if info:
            e.add_field(name="Features", value="\n".join(info))

        e.add_field(name="Channels", value="\n".join(channel_info))

        if guild.premium_tier != 0:
            boosts = f"Level {guild.premium_tier}\n{guild.premium_subscription_count} boosts"
            last_boost = max(guild.members, key=lambda m: m.premium_since or guild.created_at)
            if last_boost.premium_since is not None:
                boosts = f"{boosts}\nLast Boost: {last_boost} ({time.human_timedelta(last_boost.premium_since, accuracy=2)})"
            e.add_field(name="Boosts", value=boosts, inline=False)

        bots = sum(m.bot for m in guild.members)
        fmt = (
            f'<:Online:745077502740791366> {member_by_status["online"]} '
            f'<:Idle:745077548379013193> {member_by_status["idle"]} '
            f'<:DnD:745077524446314507> {member_by_status["dnd"]} '
            f'<:Offline:745077513826467991> {member_by_status["offline"]}\n'
            f"Total: {guild.member_count} ({formats.plural(bots):bot})"
        )

        e.add_field(name="Members", value=fmt, inline=False)
        e.add_field(
            name="Roles",
            value=", ".join(roles) if len(roles) < 10 else f"{len(roles)} roles",
        )

        emoji_stats = Counter()
        for emoji in guild.emojis:
            if emoji.animated:
                emoji_stats["animated"] += 1
                emoji_stats["animated_disabled"] += not emoji.available
            else:
                emoji_stats["regular"] += 1
                emoji_stats["disabled"] += not emoji.available

        fmt = (
            f'Regular: {emoji_stats["regular"]}/{guild.emoji_limit}\n'
            f'Animated: {emoji_stats["animated"]}/{guild.emoji_limit}\n'
        )
        if emoji_stats["disabled"] or emoji_stats["animated_disabled"]:
            fmt = f'{fmt}Disabled: {emoji_stats["disabled"]} regular, {emoji_stats["animated_disabled"]} animated\n'

        fmt = f"{fmt}Total Emoji: {len(guild.emojis)}/{guild.emoji_limit*2}"
        e.add_field(name="Emoji", value=fmt, inline=False)
        e.set_footer(text="Created").timestamp = guild.created_at
        await ctx.send(embed=e)

    async def say_permissions(self, ctx: Context, member: discord.Member, channel: MessageableGuildChannel) -> None:
        permissions = channel.permissions_for(member)
        e = discord.Embed(colour=member.colour)
        avatar = member.display_avatar.with_static_format("png")
        e.set_author(name=str(member), url=avatar)
        allowed, denied = [], []

        for name, value in permissions:
            name = name.replace("_", " ").replace("guild", "server").title()
            if value:
                allowed.append(name)
            else:
                denied.append(name)

        e.add_field(name="Allowed", value="\n".join(allowed))
        e.add_field(name="Denied", value="\n".join(denied))
        await ctx.send(embed=e)

    @commands.command()
    @commands.guild_only()
    async def permissions(
        self,
        ctx: Context,
        member: discord.Member | None = None,
        channel: MessageableGuildChannel | None = None,
    ) -> None:
        """Shows a member's permissions in a specific channel.

        If no channel is given then it uses the current one.

        You cannot use this in private messages. If no member is given then
        the info returned will be yours.
        """
        assert not isinstance(ctx.channel, discord.DMChannel)
        channel = channel or ctx.channel

        person = member or ctx.author
        assert isinstance(person, discord.Member)

        await self.say_permissions(ctx, person, channel)

    @commands.command()
    @commands.guild_only()
    @checks.admin_or_permissions(manage_roles=True)
    async def botpermissions(self, ctx: Context, *, channel: MessageableGuildChannel | None = None) -> None:
        """Shows the bot's permissions in a specific channel.

        If no channel is given then it uses the current one.

        This is a good way of checking if the bot has the permissions needed
        to execute the commands it wants to execute.

        To execute this command you must have Manage Roles permission.
        You cannot use this in private messages.
        """
        assert ctx.guild is not None

        assert not isinstance(ctx.channel, discord.DMChannel)
        channel = channel or ctx.channel

        member = ctx.guild.me
        await self.say_permissions(ctx, member, channel)

    @commands.command()
    @commands.is_owner()
    async def debugpermissions(
        self,
        ctx: Context,
        channel: MessageableGuildChannel = commands.param(converter=GuildChannel),
        author: discord.Member | None = None,
    ):
        """Shows permission resolution for a channel and an optional author."""

        person = author or ctx.author
        assert isinstance(person, discord.Member)

        await self.say_permissions(ctx, person, channel)

    """ This code and the used utils were written by and source from https://github.com/khazhyk/dango.py """

    @commands.command(name="msgraw", aliases=["msgr", "rawm"])
    @commands.cooldown(1, 15, commands.BucketType.user)
    async def raw_message(self, ctx: Context, message: discord.Message) -> None:
        """Quickly return the raw content of the specific message."""
        assert message.channel is not None
        assert isinstance(message.channel, MessageableGuildChannel)

        try:
            msg = await ctx.bot.http.get_message(message.channel.id, message.id)
        except discord.NotFound as err:
            raise commands.BadArgument(
                f"Message with the ID of {message.id} cannot be found in {message.channel.mention}."
            ) from err

        await ctx.send(
            f"```json\n{formats.clean_triple_backtick(formats.escape_invis_chars(json.dumps(msg, indent=2, ensure_ascii=False, sort_keys=True)))}\n```"
        )

    @commands.check(lambda ctx: bool(ctx.guild and ctx.guild.voice_client))
    @commands.command(name="disconnect")
    async def disconnect_(self, ctx: Context) -> None:
        """Disconnects the bot from the voice channel."""
        assert ctx.guild is not None  # guarded by check
        assert ctx.guild.voice_client is not None  # guarded by check

        v_client: discord.VoiceClient = ctx.guild.voice_client  # type: ignore # python types are gae
        v_client.stop()
        await v_client.disconnect(force=True)


async def setup(bot: Kukiko) -> None:
    await bot.add_cog(Meta(bot))
