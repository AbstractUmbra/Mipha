"""
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at http://mozilla.org/MPL/2.0/.
"""

from __future__ import annotations

import contextlib
import contextvars
import datetime  # noqa: TC003 # dpy needs this at runtime
import inspect
import os
import traceback
import unicodedata
from collections import Counter
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from utilities.context import Context, GuildContext, Interaction
from utilities.shared import checks, formats
from utilities.shared.converters import DatetimeTransformer  # noqa: TC001 # dpy needs this at runtime
from utilities.shared.formats import ts

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from bot import Mipha

type GuildChannel = (
    discord.TextChannel
    | discord.VoiceChannel
    | discord.StageChannel
    | discord.CategoryChannel
    | discord.Thread
    | discord.ForumChannel
)
type MessageableGuildChannel = discord.TextChannel | discord.Thread | discord.VoiceChannel


class Prefix(commands.Converter):
    async def convert(self, ctx: Context, argument: str) -> str:
        assert ctx.bot.user is not None

        user_id = ctx.bot.user.id
        if argument.startswith((f"<@{user_id}>", f"<@!{user_id}>")):
            raise commands.BadArgument("That is a reserved prefix already in use.")
        return argument


_current = contextvars.ContextVar[Interaction]("_current")


class PatchedContext(Context):
    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN003, ANN002
        super().__init__(*args, **kwargs)
        self.first_interaction_sent: bool = False

    async def send(self, content: str | None = None, **kwargs) -> discord.Message | None:  # noqa: ANN003
        if not self.first_interaction_sent:
            self.first_interaction_sent = True

            kwargs.pop("allowed_mentions", None)
            kwargs.pop("ephemeral", None)

            await _current.get().response.send_message(content=content, ephemeral=False, **kwargs)
            return None
        return await super().send(content=content, **kwargs)

    @contextlib.asynccontextmanager
    async def typing(self, *_, **__) -> AsyncGenerator[None]:  # noqa: ANN003, ANN002
        yield


class Meta(commands.Cog):  # noqa: PLR0904
    """Commands for utilities related to Discord or the Bot itself."""

    def __init__(self, bot: Mipha) -> None:
        self.bot = bot
        self.interpret_as_command_ctx_menu = app_commands.ContextMenu(
            name="Interpret as Command",
            callback=self.interpret_as_command_callback,
        )
        self.bot.tree.add_command(self.interpret_as_command_ctx_menu)

    async def cog_command_error(self, ctx: Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.BadArgument):
            await ctx.send(str(error))

    def cog_unload(self) -> None:
        self.bot.tree.remove_command(self.interpret_as_command_ctx_menu.name, type=self.interpret_as_command_ctx_menu.type)

    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=False, users=True)
    async def interpret_as_command_callback(self, interaction: Interaction, message: discord.Message, /) -> None:
        if message.author.bot:
            await interaction.response.send_message(
                "Sorry I won't invoke commands based on a bot's messages.",
                ephemeral=True,
            )
            return

        if interaction.user.id != message.author.id:
            await interaction.response.send_message(content="Sorry, this is not your message.", ephemeral=True)
            return

        context = await self.bot.get_context(message, cls=PatchedContext)

        if not context.valid:
            await interaction.response.send_message(
                content="Sorry this doesn't look like a command for me.",
                ephemeral=True,
            )
            return

        _current.set(interaction)
        ticks = "`" * 3

        try:
            await context.command.invoke(context)
        except (
            commands.UserInputError,
            commands.CheckFailure,
            commands.DisabledCommand,
            commands.CommandOnCooldown,
            commands.MaxConcurrencyReached,
        ) as err:
            await interaction.response.send_message(content=f"{type(err).__name__}: {err}")
        except Exception as err:
            info = "".join(traceback.format_exception(type(err), err, err.__traceback__, 2))
            await interaction.response.send_message(content=f"Some exception occurred, sorry:-\n{ticks}py\n{info}\n{ticks}")
            raise

        if not context.first_interaction_sent:
            await interaction.response.send_message(content="Command finished with no output.", ephemeral=True)

        return

    @app_commands.command()
    @app_commands.describe(
        when="When to show a timestamp for, accepts 'tomorrow at 7pm' etc.",
        ephemeral="Whether to show the whole channel or just you.",
    )
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    async def timestamp(
        self,
        interaction: discord.Interaction,
        when: app_commands.Transform[datetime.datetime, DatetimeTransformer],
        ephemeral: bool = True,  # noqa: FBT001, FBT002 # required for d.py callbacks
    ) -> None:
        """
        Enter a date and/or time to get a discord formatted datetime for it.
        Accepts friendly input like 'tomorrow at 3:30pm'.
        """
        ret = ["`{0:{spec}}` -> {0:{spec}}".format(ts(when), spec=fmt) for fmt in ("t", "T", "D", "f", "F", "R")]
        ret.insert(0, "\u200b\n")
        await interaction.response.send_message("\n".join(ret), ephemeral=ephemeral)

    @commands.command()
    async def ping(self, ctx: Context) -> None:
        """Ping commands are stupid."""
        await ctx.send("Ping commands are stupid.")

    @commands.command()
    async def charinfo(self, ctx: Context, *, characters: str) -> None:
        """Shows you information about a number of characters.

        Only up to 25 characters at a time.
        """

        def to_string(c: str) -> str:
            digit = f"{ord(c):x}"
            name = unicodedata.name(c, "Name not found.")
            return f"[`\\U{digit:>08}`](http://www.fileformat.info/info/unicode/char/{digit}): {name} **\N{EM DASH}** {c}"

        msg = "\n".join(map(to_string, characters))
        await ctx.send(msg, suppress_embeds=True)

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
    @checks.is_manager()
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
        except commands.TooManyArguments as e:
            await ctx.send(f"{ctx.tick(False)} {e}")  # noqa: FBT003 # shortcut
        else:
            await ctx.send(ctx.tick(True))  # noqa: FBT003 # shortcut

    @prefix_add.error
    async def prefix_add_error(self, ctx: Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.TooManyArguments):
            await ctx.send("You've given too many prefixes. Either quote it or only do it one by one.")

    @prefix.command(name="remove", aliases=["delete"], ignore_extra=False)
    @checks.is_manager()
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
        except commands.TooManyArguments as e:
            await ctx.send(f"{ctx.tick(False)} {e}")  # noqa: FBT003 # shortcut
        else:
            await ctx.send(ctx.tick(True))  # noqa: FBT003 # shortcut

    @prefix.command(name="clear")
    @checks.is_mod()
    async def prefix_clear(self, ctx: Context) -> None:
        """Removes all custom prefixes.

        After this, the bot will listen to only mention prefixes.

        You must have Manage Server permission to use this command.
        """
        assert ctx.guild is not None

        await self.bot._set_guild_prefixes(ctx.guild, [])
        await ctx.send(ctx.tick(True))  # noqa: FBT003 # shortcut

    @commands.command()
    async def source(self, ctx: Context, *, command: str | None = None) -> None:
        """Displays my full source code or for a specific command.

        To display the source code of a subcommand you can separate it by
        periods, e.g. tag.create for the create subcommand of the tag command
        or by spaces.
        """
        source_url = "https://github.com/AbstractUmbra/mipha"
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
    async def serverinfo(self, ctx: GuildContext, *, guild_id: int | None = None) -> None:  # noqa: PLR0914, PLR0915
        """Shows info about the current server."""

        if guild_id is not None and await self.bot.is_owner(ctx.author):
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                await ctx.send("Invalid Guild ID given.")
                return
        else:
            guild = ctx.guild

        roles = [role.name.replace("@", "@\u200b") for role in guild.roles]

        if not guild.chunked:
            async with ctx.typing():
                await guild.chunk(cache=True)

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
            if not perms.read_messages or (
                isinstance(channel, discord.VoiceChannel) and (not perms.connect or not perms.speak)
            ):
                secret[channel_type] += 1

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
        all_features = {
            "PARTNERED": "Partnered",
            "VERIFIED": "Verified",
            "DISCOVERABLE": "Server Discovery",
            "COMMUNITY": "Community Server",
            "FEATURABLE": "Featured",
            "WELCOME_SCREEN_ENABLED": "Welcome Screen",
            "INVITE_SPLASH": "Invite Splash",
            "VIP_REGIONS": "VIP Voice Servers",
            "VANITY_URL": "Vanity Invite",
            "COMMERCE": "Commerce",
            "LURKABLE": "Lurkable",
            "NEWS": "News Channels",
            "ANIMATED_ICON": "Animated Icon",
            "BANNER": "Banner",
        }

        for feature, label in all_features.items():
            if feature in features:
                info.append(f"{ctx.tick(True)}: {label}")  # noqa: FBT003 # shortcut

        if info:
            e.add_field(name="Features", value="\n".join(info))

        e.add_field(name="Channels", value="\n".join(channel_info))

        if guild.premium_tier != 0:
            boosts = f"Level {guild.premium_tier}\n{guild.premium_subscription_count} boosts"
            last_boost = max(guild.members, key=lambda m: m.premium_since or guild.created_at)
            if last_boost.premium_since is not None:
                boosts = f"{boosts}\nLast Boost: {last_boost} ({discord.utils.format_dt(last_boost.premium_since, 'R')})"
            e.add_field(name="Boosts", value=boosts, inline=False)

        bots = sum(m.bot for m in guild.members)
        fmt = f"Total: {guild.member_count} ({formats.plural(bots):bot})"

        e.add_field(name="Members", value=fmt, inline=False)
        e.add_field(name="Roles", value=", ".join(roles) if len(roles) < 10 else f"{len(roles)} roles")

        emoji_stats = Counter()
        for emoji in guild.emojis:
            if emoji.animated:
                emoji_stats["animated"] += 1
                emoji_stats["animated_disabled"] += not emoji.available
            else:
                emoji_stats["regular"] += 1
                emoji_stats["disabled"] += not emoji.available

        fmt = (
            f"Regular: {emoji_stats['regular']}/{guild.emoji_limit}\n"
            f"Animated: {emoji_stats['animated']}/{guild.emoji_limit}\n"
        )
        if emoji_stats["disabled"] or emoji_stats["animated_disabled"]:
            fmt = f"{fmt}Disabled: {emoji_stats['disabled']} regular, {emoji_stats['animated_disabled']} animated\n"

        fmt = f"{fmt}Total Emoji: {len(guild.emojis)}/{guild.emoji_limit * 2}"
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
            name = name.replace("_", " ").replace("guild", "server").title()  # noqa: PLW2901 # correct usage
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
        channel: MessageableGuildChannel = commands.param(converter=GuildChannel),  # noqa: B008 # this is how commands.param works
        author: discord.Member | None = None,
    ) -> None:
        """Shows permission resolution for a channel and an optional author."""

        person = author or ctx.author
        assert isinstance(person, discord.Member)

        await self.say_permissions(ctx, person, channel)

    """ This code and the used utils were written by and source from https://github.com/khazhyk/dango.py """

    @commands.command(name="msgraw", aliases=["msgr", "rawm"])
    @commands.cooldown(1, 15, commands.BucketType.user)
    async def raw_message(self, ctx: Context, message: discord.Message | None = None) -> None:
        """Quickly return the raw content of the specific message."""
        message = message or ctx.replied_message
        if not message:
            await ctx.send("Missing a message to fetch information from.")
            return

        assert message.channel is not None

        try:
            msg = await ctx.bot.http.get_message(message.channel.id, message.id)
        except discord.NotFound as err:
            msg = f"Message with the ID of {message.id} cannot be found in <#{message.channel.id}>."
            raise commands.BadArgument(msg) from err

        # msg["content"] = msg["content"].replace("ð", "d").replace("Ð", "D").replace("þ", "th").replace("Þ", "Th")  # noqa: E501, ERA001
        # thanks daggy

        await ctx.send(
            f"```json\n{formats.clean_triple_backtick(formats.escape_invis_chars(formats.to_json(msg)))}\n```",
        )

    @commands.check(lambda ctx: bool(ctx.guild and ctx.guild.voice_client))
    @commands.command(name="disconnect")
    async def disconnect_(self, ctx: GuildContext) -> None:
        """Disconnects the bot from the voice channel."""
        assert ctx.guild.voice_client is not None  # guarded by check

        v_client: discord.VoiceClient = ctx.guild.voice_client  # pyright: ignore[reportAssignmentType] # type downcasting
        v_client.stop()
        await v_client.disconnect(force=True)


async def setup(bot: Mipha) -> None:
    await bot.add_cog(Meta(bot))
