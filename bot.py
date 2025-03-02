"""
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at http://mozilla.org/MPL/2.0/.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import pathlib
import secrets
import traceback
from collections import Counter, deque
from logging.handlers import RotatingFileHandler
from typing import TYPE_CHECKING, Any, Literal, overload

import aiohttp
import asyncpg
import discord
import jishaku
import mystbin
from discord import app_commands
from discord.ext import commands
from discord.utils import (
    _ColourFormatter as ColourFormatter,  # noqa: PLC2701 # we do a little cheating
    stream_supports_colour,
)

try:
    import uvloop
except ModuleNotFoundError:
    RUNTIME = asyncio.run
else:
    RUNTIME = uvloop.run

from extensions import EXTENSIONS
from utilities.context import Context, Interaction
from utilities.prefix import callable_prefix as _callable_prefix
from utilities.shared.async_config import Config
from utilities.shared.db import db_init
from utilities.shared.formats import to_json
from utilities.shared.timezones import TimezoneHandler

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Coroutine, Iterable
    from typing import Self

    from discord.ext.commands._types import ContextT

    from extensions.config import Config as ConfigCog
    from extensions.reminders import Reminder
    from utilities._types.config import RootConfig

jishaku.Flags.HIDE = True
jishaku.Flags.RETAIN = True
jishaku.Flags.NO_UNDERSCORE = True
jishaku.Flags.NO_DM_TRACEBACK = True
INTENTS = discord.Intents.all()

CONFIG_PATH = pathlib.Path("configs/bot_config.json")


class MiphaCommandTree(app_commands.CommandTree):
    client: Mipha
    _mention_app_commands: dict[int | None, list[app_commands.AppCommand]]

    async def sync(self, *, guild: discord.abc.Snowflake | None = None) -> list[app_commands.AppCommand]:
        """Method overwritten to store the commands."""
        ret = await super().sync(guild=guild)
        self._mention_app_commands[guild.id if guild else None] = ret
        return ret

    async def fetch_commands(self, *, guild: discord.abc.Snowflake | None = None) -> list[app_commands.AppCommand]:
        """Method overwritten to store the commands."""
        ret = await super().fetch_commands(guild=guild)
        self._mention_app_commands[guild.id if guild else None] = ret
        return ret

    async def find_mention_for(
        self,
        command: app_commands.Command | app_commands.Group | str,
        *,
        guild: discord.abc.Snowflake | None = None,
    ) -> str | None:
        """Retrieves the mention of an AppCommand given a specific command name, and optionally, a guild.
        Parameters
        ----------
        name: Union[:class:`app_commands.Command`, :class:`app_commands.Group`, str]
            The command which it's mention we will attempt to retrieve.
        guild: Optional[:class:`discord.abc.Snowflake`]
            The scope (guild) from which to retrieve the commands from. If None is given or not passed,
            only the global scope will be searched, however the global scope will also be searched if
            a guild is passed.
        """

        check_global = self.fallback_to_global is True or guild is not None

        if isinstance(command, str):
            # Try and find a command by that name. discord.py does not return children from tree.get_command, but
            # using walk_commands and utils.get is a simple way around that.
            resolved = discord.utils.get(self.walk_commands(guild=guild), qualified_name=command)

            if check_global and not resolved:
                resolved = discord.utils.get(self.walk_commands(), qualified_name=command)

        else:
            resolved = command

        if not resolved:
            return None

        if guild:
            try:
                local_commands = self._mention_app_commands[guild.id]
            except KeyError:
                local_commands = await self.fetch_commands(guild=guild)

            app_command_found = discord.utils.get(local_commands, name=(resolved.root_parent or resolved).name)

        else:
            app_command_found = None

        if check_global and not app_command_found:
            try:
                global_commands = self._mention_app_commands[None]
            except KeyError:
                global_commands = await self.fetch_commands()

            app_command_found = discord.utils.get(global_commands, name=(resolved.root_parent or resolved).name)

        if not app_command_found:
            return None

        return f"</{resolved.qualified_name}:{app_command_found.id}>"

    async def on_error(
        self,
        interaction: Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        assert interaction.command is not None  # typechecking # disable assertions

        self.client.log_handler.log.exception("Exception occurred in the CommandTree:\n%s", exc_info=error)

        e = discord.Embed(title="Command Error", colour=0xA32952)
        e.add_field(name="Command", value=(interaction.command and interaction.command.name) or "No command found.")
        e.add_field(name="Author", value=interaction.user, inline=False)
        channel = interaction.channel
        assert channel  # always there
        guild = interaction.guild
        channel_name = "In DMs" if isinstance(channel, discord.DMChannel) else channel.name
        location_fmt = f"Channel: {channel_name} ({channel.id})"
        if guild:
            location_fmt += f"\nGuild: {guild.name} ({guild.id})"
        e.add_field(name="Location", value=location_fmt, inline=True)
        (exc_type, exc, tb) = type(error), error, error.__traceback__
        trace = traceback.format_exception(exc_type, exc, tb)
        clean = "".join(trace)
        if len(clean) >= 2000:
            password = secrets.token_urlsafe(16)
            paste = await interaction.client.create_paste(content=clean, password=password)
            e.description = (
                f"Error was too long to send in a codeblock, so I have pasted it [here]({paste})."
                f"\nThe password is `{password}`."
            )
        else:
            e.description = f"```py\n{clean}\n```"
        e.timestamp = datetime.datetime.now(datetime.UTC)
        await self.client.logging_webhook.send(embed=e)
        await self.client.owner.send(embed=e)


class RemoveNoise(logging.Filter):
    def __init__(self) -> None:
        super().__init__(name="discord.state")

    def filter(self, record: logging.LogRecord) -> bool:
        return not (record.levelname == "WARNING" and "referencing an unknown" in record.msg)


class ProxyObject(discord.Object):
    __slots__ = ("guild",)

    def __init__(self, guild: discord.abc.Snowflake | None, /) -> None:
        super().__init__(id=0)
        self.guild: discord.abc.Snowflake | None = guild


class LogHandler:
    def __init__(self, *, stream: bool = True) -> None:
        self.log: logging.Logger = logging.getLogger()
        self.max_bytes: int = 32 * 1024 * 1024
        self.logging_path = pathlib.Path("./logs/")
        self.logging_path.mkdir(exist_ok=True)
        self.stream: bool = stream

        self.info = self.log.info
        self.error = self.log.error
        self.warning = self.log.warning
        self.debug = self.log.debug

    async def __aenter__(self) -> Self:
        return self.__enter__()

    def __enter__(self: Self) -> Self:
        logging.getLogger("discord").setLevel(logging.INFO)
        logging.getLogger("discord.http").setLevel(logging.INFO)
        logging.getLogger("discord.ext.tasks").setLevel(logging.INFO)
        logging.getLogger("hondana.http").setLevel(logging.INFO)
        logging.getLogger("discord.state").addFilter(RemoveNoise())

        self.log.setLevel(logging.INFO)
        handler = RotatingFileHandler(
            filename=self.logging_path / "Mipha.log",
            encoding="utf-8",
            mode="w",
            maxBytes=self.max_bytes,
            backupCount=5,
        )
        dt_fmt = "%Y-%m-%d %H:%M:%S"
        fmt = logging.Formatter("[{asctime}] [{levelname:<7}] {name}: {message}", dt_fmt, style="{")
        handler.setFormatter(fmt)
        self.log.addHandler(handler)

        if self.stream:
            stream_handler = logging.StreamHandler()
            if stream_supports_colour(stream_handler):
                stream_handler.setFormatter(ColourFormatter())
            self.log.addHandler(stream_handler)

        return self

    async def __aexit__(self, *args: object) -> None:
        return self.__exit__(*args)

    def __exit__(self, *args: object) -> None:
        handlers = self.log.handlers[:]
        for hdlr in handlers:
            hdlr.close()
            self.log.removeHandler(hdlr)


class Mipha(commands.Bot):
    """Mipha's bot class."""

    log_handler: LogHandler
    pool: asyncpg.Pool[asyncpg.Record]
    user: discord.ClientUser
    session: aiohttp.ClientSession
    start_time: datetime.datetime
    command_stats: Counter[str]
    socket_stats: Counter[Any]
    command_types_used: Counter[bool]
    bot_app_info: discord.AppInfo
    mb_client: mystbin.Client
    tree: MiphaCommandTree
    _original_help_command: commands.HelpCommand | None  # for help command overriding
    _stats_cog_gateway_handler: logging.Handler
    tz_handler: TimezoneHandler

    __slots__ = (
        "_blacklist_data",
        "_error_handling_cooldown",
        "_original_help_command",
        "_prefix_data",
        "_previous_websocket_events",
        "_spam_cooldown_mapping",
        "_spammer_count",
        "_stats_cog_gateway_handler",
        "command_stats",
        "log_handler",
        "pool",
        "session",
        "socket_stats",
        "start_time",
    )

    def __init__(self, config: RootConfig) -> None:
        super().__init__(
            command_prefix=_callable_prefix,
            tree_cls=MiphaCommandTree,
            description="Hello, I'm a fun discord bot for Umbra#0009's personal use.",
            intents=INTENTS,
            allowed_mentions=discord.AllowedMentions.none(),
            strip_after_prefix=True,
        )
        self.tree._mention_app_commands = {}

        self.config: RootConfig = config
        self.dev_guilds: list[discord.Object] = [
            discord.Object(id=item, type=discord.Guild) for item in config["bot"].get("dev_guilds", [])
        ]

        self._prefix_data: Config[list[str]] = Config(pathlib.Path("configs/prefixes.json"))
        self._blacklist_data: Config[bool] = Config(pathlib.Path("configs/blacklist.json"))

        # auto spam detection
        self._spam_cooldown_mapping: commands.CooldownMapping = commands.CooldownMapping.from_cooldown(
            10,
            12.0,
            commands.BucketType.user,
        )
        self._spammer_count: Counter = Counter()

        # misc logging
        self._previous_websocket_events: deque = deque(maxlen=10)
        self._error_handling_cooldown: commands.CooldownMapping = commands.CooldownMapping.from_cooldown(
            1,
            5,
            commands.BucketType.user,
        )
        self.command_stats = Counter()
        self.socket_stats = Counter()
        self.owner_id: int | None = None
        self.owner_ids: Iterable[int] = self.config["bot"]["owner_ids"]

    def run(self) -> None:
        raise NotImplementedError("Please use `.start()` instead.")

    @property
    def owner(self) -> discord.User:
        return self.bot_app_info.owner

    @property
    def reminder(self) -> Reminder | None:
        return self.get_cog("Reminder")  # pyright: ignore[reportReturnType] # type downcasting

    @property
    def config_cog(self) -> ConfigCog | None:
        return self.get_cog("Config")  # pyright: ignore[reportReturnType] # type downcasting

    @discord.utils.cached_property
    def logging_webhook(self) -> discord.Webhook:
        return discord.Webhook.from_url(self.config["webhooks"]["logging"], session=self.session)

    def update_config(self) -> None:
        config = CONFIG_PATH.read_text("utf-8")
        raw_cfg: RootConfig = discord.utils._from_json(config)

        self.config = raw_cfg

    async def on_socket_response(self, message: Any) -> None:
        """Quick override to log websocket events."""
        self._previous_websocket_events.append(message)

    async def on_ready(self) -> None:
        self.log_handler.log.info("%s got a ready event at %s", self.user.name, datetime.datetime.now(datetime.UTC))

    async def on_resume(self) -> None:
        self.log_handler.log.info("%s got a resume event at %s", self.user.name, datetime.datetime.now(datetime.UTC))

    async def on_command_error(self, ctx: Context, error: commands.CommandError) -> None:
        assert ctx.command is not None  # type checking - disable assertions
        if isinstance(error, commands.NoPrivateMessage):
            retry_period = self._error_handling_cooldown.update_rate_limit(ctx.message)
            if retry_period is None:
                return
            await ctx.send("Sorry, this command is not available in DMs.")
            return

        if isinstance(error, commands.DisabledCommand):
            retry_period = self._error_handling_cooldown.update_rate_limit(ctx.message)
            if retry_period is None:
                return
            await ctx.send("Sorry, this command has been disabled.")
            return

        if isinstance(error, commands.CommandInvokeError):
            origin_ = error.original
            if not isinstance(origin_, discord.HTTPException):
                self.log_handler.error("In %s:", ctx.command.qualified_name, exc_info=origin_)

    def _get_guild_prefixes(
        self,
        guild: discord.abc.Snowflake,
        *,
        local_: Callable[[Self, discord.Message], list[str]] = _callable_prefix,
        raw: bool = False,
    ) -> list[str]:
        if raw:
            return self._prefix_data.get(guild.id, ["hey babe "])

        snowflake_proxy = ProxyObject(guild)
        return local_(self, snowflake_proxy)  # pyright: ignore[reportArgumentType] # lying here

    async def _set_guild_prefixes(self, guild: discord.abc.Snowflake, prefixes: list[str] | None) -> None:
        if not prefixes:
            await self._prefix_data.put(guild.id, [])
        elif len(prefixes) > 10:
            raise commands.errors.TooManyArguments("Cannot have more than 10 custom prefixes.")
        else:
            await self._prefix_data.put(guild.id, prefixes)

    async def _blacklist_add(self, object_id: int) -> None:
        await self._blacklist_data.put(object_id, True)  # noqa: FBT003 # shortcut

    async def _blacklist_remove(self, object_id: int) -> None:
        try:
            await self._blacklist_data.remove(object_id)
        except KeyError:
            pass

    @overload
    def _log_spammer(
        self,
        ctx: Context,
        message: discord.Message,
        retry_after: float,
        *,
        autoblock: Literal[True],
    ) -> Coroutine[None, None, discord.WebhookMessage]: ...

    @overload
    def _log_spammer(
        self,
        ctx: Context,
        message: discord.Message,
        retry_after: float,
        *,
        autoblock: Literal[False],
    ) -> None: ...

    @overload
    def _log_spammer(self, ctx: Context, message: discord.Message, retry_after: float, *, autoblock: bool = ...) -> None: ...

    def _log_spammer(
        self,
        ctx: Context,
        message: discord.Message,
        retry_after: float,
        *,
        autoblock: bool = False,
    ) -> Coroutine[None, None, discord.WebhookMessage] | None:
        guild_name = getattr(ctx.guild, "name", "No Guild (DMs)")
        guild_id = getattr(ctx.guild, "id", None)
        fmt = "User %s (ID %s) in guild %r (ID %s) is spamming. retry_after: %.2fs"
        self.log_handler.log.warning(fmt, message.author, message.author.id, guild_name, guild_id, retry_after)
        if not autoblock:
            return None

        embed = discord.Embed(title="Autoblocked Member", colour=0xDDA453)
        embed.add_field(name="User", value=f"{message.author} (ID {message.author.id})", inline=False)
        if guild_id is not None:
            embed.add_field(name="Guild Info", value=f"{guild_name} (ID {guild_id})", inline=False)
        embed.add_field(name="Channel Info", value=f"{message.channel} (ID: {message.channel.id}", inline=False)
        embed.timestamp = datetime.datetime.now(datetime.UTC)

        return self.logging_webhook.send(embed=embed, wait=True)

    async def get_or_fetch_member(self, guild: discord.Guild, member_id: int) -> discord.Member | None:
        member = guild.get_member(member_id)
        if member:
            return member

        try:
            member = await guild.fetch_member(member_id)
        except discord.NotFound:
            return None
        return member

    async def resolve_member_ids(self, guild: discord.Guild, member_ids: Iterable[int]) -> AsyncIterator[discord.Member]:
        needs_resolution: list[int] = []
        for member_id in member_ids:
            member = guild.get_member(member_id)
            if member is not None:
                yield member
            else:
                needs_resolution.append(member_id)

        total_need_resolution = len(needs_resolution)
        if total_need_resolution == 1:
            members = await guild.query_members(limit=1, user_ids=needs_resolution, cache=True)
            if members:
                yield members[0]
        elif total_need_resolution <= 100:
            # Only a single resolution call needed here
            resolved = await guild.query_members(limit=100, user_ids=needs_resolution, cache=True)
            for member in resolved:
                yield member
        else:
            # We need to chunk these in bits of 100...
            for index in range(0, total_need_resolution, 100):
                to_resolve = needs_resolution[index : index + 100]
                members = await guild.query_members(limit=100, user_ids=to_resolve, cache=True)
                for member in members:
                    yield member

    @overload
    async def get_context(self, origin: Interaction | discord.Message, /) -> Context: ...

    @overload
    async def get_context(self, origin: Interaction | discord.Message, /, *, cls: type[ContextT]) -> ContextT: ...

    async def get_context(self, origin: Interaction | discord.Message, /, *, cls: type[ContextT] = Context) -> ContextT:
        return await super().get_context(origin, cls=cls)

    async def process_commands(self, message: discord.Message, /) -> None:
        ctx = await self.get_context(message)

        if ctx.command is None:
            return

        if ctx.author.id in self._blacklist_data:
            return

        if ctx.guild is not None and ctx.guild.id in self._blacklist_data:
            return

        bucket = self._spam_cooldown_mapping.get_bucket(message)
        if not bucket:
            return
        current = message.created_at.timestamp()
        retry_after = bucket.update_rate_limit(current)
        if retry_after and message.author.id != self.owner_id:
            self._spammer_count[message.author.id] += 1
            if self._spammer_count[message.author.id] >= 5:
                await self._blacklist_add(message.author.id)
                await self._log_spammer(ctx, message, retry_after, autoblock=True)
                del self._spammer_count[message.author.id]
            else:
                self._log_spammer(ctx, message, retry_after)
            return
        self._spammer_count.pop(message.author.id, None)

        await self.invoke(ctx)

    async def on_message(self, message: discord.Message, /) -> None:
        if message.author.bot:
            return

        await self.process_commands(message)

    async def on_message_edit(self, before: discord.Message, after: discord.Message, /) -> None:
        if not before.embeds and after.embeds:
            return

        can_edit = False
        if self.owner_ids:
            if after.author.id in self.owner_ids:
                can_edit = True
        else:
            if after.author.id == self.owner_id:
                can_edit = True

        if before.flags.suppress_embeds != after.flags.suppress_embeds:
            can_edit = False

        if can_edit:
            await self.process_commands(after)

    async def on_guild_join(self, guild: discord.Guild) -> None:
        """When the bot joins a guild."""
        if guild.id in self._blacklist_data:
            await guild.leave()

    async def start(self) -> None:
        try:
            await super().start(token=self.config["bot"]["token"], reconnect=True)
        finally:
            path = pathlib.Path("logs/prev_events.log")
            with path.open("w+", encoding="utf-8") as f:  # noqa: ASYNC230 # this is as the loop exists
                for event in self._previous_websocket_events:
                    try:
                        last_log = to_json(event)
                    except Exception:  # noqa: BLE001 # orjson or jsondecodeerror
                        f.write(f"{event}\n")
                    else:
                        f.write(f"{last_log}\n")

    async def _reload_tz_handler(self) -> None:
        self.tz_handler = await TimezoneHandler.startup(session=self.session)

    async def setup_hook(self) -> None:
        self.start_time: datetime.datetime = datetime.datetime.now(datetime.UTC)

        self.bot_app_info = await self.application_info()
        self.mb_client = mystbin.Client(session=self.session)
        await self._reload_tz_handler()

    async def create_paste(
        self,
        *,
        content: str | None = None,
        files: list[tuple[str, str]] | None = None,
        password: str | None = None,
        expires: datetime.datetime | None = None,
    ) -> str:
        if not content and not files:
            raise ValueError("Either `content` or `files` must be provided.")

        if content:
            post_files = [mystbin.File(filename="output.py", content=content)]
        elif files:
            post_files = [mystbin.File(filename=name, content=content) for name, content in files]
        else:
            raise ValueError("An argument for `content` or `files` must be provided.")

        paste = await self.mb_client.create_paste(files=post_files, password=password, expires=expires)

        return paste.url


async def main() -> None:
    config = CONFIG_PATH.read_text("utf-8")
    raw_cfg: RootConfig = discord.utils._from_json(config)

    async with (
        Mipha(raw_cfg) as bot,
        aiohttp.ClientSession(json_serialize=discord.utils._to_json) as session,
        asyncpg.create_pool(
            host=bot.config["postgresql"]["host"],
            user=bot.config["postgresql"]["user"],
            password=bot.config["postgresql"]["password"],
            database=bot.config["postgresql"]["database"],
            port=bot.config["postgresql"]["port"],
            command_timeout=60,
            max_inactive_connection_lifetime=0,
            init=db_init,
        ) as pool,
        LogHandler() as log_handler,
    ):
        bot.log_handler = log_handler
        bot.pool = pool

        bot.session = session

        await bot.load_extension("jishaku")
        for extension in EXTENSIONS:
            await bot.load_extension(extension.name)
            bot.log_handler.log.info("Loaded %sextension: %s", "module " if extension.ispkg else "", extension.name)

        await bot.start()


if __name__ == "__main__":
    RUNTIME(main())
