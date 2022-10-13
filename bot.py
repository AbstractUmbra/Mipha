"""
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at http://mozilla.org/MPL/2.0/.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import pathlib
import sys
import traceback
from collections import Counter, deque
from logging.handlers import RotatingFileHandler
from typing import TYPE_CHECKING, Any, Callable, Coroutine, Literal, overload

import aiohttp
import asyncpg
import discord
import hondana
import jishaku
import mystbin
import nhentaio
from discord import app_commands
from discord.ext import commands
from discord.utils import _ColourFormatter as ColourFormatter, stream_supports_colour
from typing_extensions import Self

import _bot_config
from utilities.async_config import Config
from utilities.context import Context
from utilities.db import db_init
from utilities.prefix import callable_prefix as _callable_prefix


if TYPE_CHECKING:
    from extensions.reminders import Reminder

LOGGER = logging.getLogger("Kukiko")
jishaku.Flags.HIDE = True
jishaku.Flags.RETAIN = True
jishaku.Flags.NO_UNDERSCORE = True
jishaku.Flags.NO_DM_TRACEBACK = True
INTENTS = discord.Intents(_bot_config.INTENTS)


class KukikoCommandTree(app_commands.CommandTree):
    client: Kukiko

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        assert interaction.command is not None  # typechecking # disable assertions

        LOGGER.exception("Exception occurred in the CommandTree:\n%s", error)

        e = discord.Embed(title="Command Error", colour=0xA32952)
        e.add_field(name="Command", value=interaction.command.name)
        e.add_field(name="Author", value=interaction.user, inline=False)
        channel = interaction.channel
        guild = interaction.guild
        location_fmt = f"Channel: {channel.name} ({channel.id})"  # type: ignore
        if guild:
            location_fmt += f"\nGuild: {guild.name} ({guild.id})"
        e.add_field(name="Location", value=location_fmt, inline=True)
        (exc_type, exc, tb) = type(error), error, error.__traceback__
        trace = traceback.format_exception(exc_type, exc, tb)
        clean = "".join(trace)
        e.description = f"```py\n{clean}\n```"
        e.timestamp = datetime.datetime.now(datetime.timezone.utc)
        await self.client.logging_webhook.send(embed=e)
        await self.client.owner.send(embed=e)


class RemoveNoise(logging.Filter):
    def __init__(self):
        super().__init__(name="discord.state")

    def filter(self, record):
        if record.levelname == "WARNING" and "referencing an unknown" in record.msg:
            return False
        return True


class SetupLogging:
    def __init__(self, *, stream: bool = True) -> None:
        self.log: logging.Logger = logging.getLogger()
        self.max_bytes: int = 32 * 1024 * 1024
        self.logging_path = pathlib.Path("./logs/")
        self.logging_path.mkdir(exist_ok=True)
        self.stream: bool = stream

    def __enter__(self: Self) -> Self:
        logging.getLogger("discord").setLevel(logging.INFO)
        logging.getLogger("discord.http").setLevel(logging.INFO)
        logging.getLogger("hondana.http").setLevel(logging.INFO)
        logging.getLogger("discord.state").addFilter(RemoveNoise())

        self.log.setLevel(logging.INFO)
        handler = RotatingFileHandler(
            filename=self.logging_path / "Kukiko.log", encoding="utf-8", mode="w", maxBytes=self.max_bytes, backupCount=5
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

    def __exit__(self, *args: Any) -> None:
        handlers = self.log.handlers[:]
        for hdlr in handlers:
            hdlr.close()
            self.log.removeHandler(hdlr)


class Kukiko(commands.Bot):
    """Kukiko's bot class."""

    pool: asyncpg.Pool
    user: discord.ClientUser
    session: aiohttp.ClientSession
    mb_client: mystbin.Client
    md_client: hondana.Client
    h_client: nhentaio.Client
    start_time: datetime.datetime
    command_stats: Counter[str]
    socket_stats: Counter[Any]
    command_types_used: Counter[bool]
    bot_app_info: discord.AppInfo
    _original_help_command: commands.HelpCommand | None  # for help command overriding
    _stats_cog_gateway_handler: logging.Handler

    __slots__ = (
        "session",
        "h_client",
        "mb_client",
        "md_client",
        "start_time",
        "pool",
        "command_stats",
        "socket_stats",
        "_blacklist_data",
        "_prefix_data",
        "_spam_cooldown_mapping",
        "_spammer_count",
        "_previous_websocket_events",
        "_error_handling_cooldown",
        "_original_help_command",
        "_stats_cog_gateway_handler",
    )

    def __init__(self):
        super().__init__(
            command_prefix=_callable_prefix,
            tree_cls=KukikoCommandTree,
            description="Hello, I'm a fun discord bot for Umbra#0009's personal use.",
            intents=INTENTS,
            allowed_mentions=discord.AllowedMentions.none(),
        )

        self._prefix_data: Config[list[str]] = Config(pathlib.Path("configs/prefixes.json"))
        self._blacklist_data: Config[list[str]] = Config(pathlib.Path("configs/blacklist.json"))

        # auto spam detection
        self._spam_cooldown_mapping: commands.CooldownMapping = commands.CooldownMapping.from_cooldown(
            10, 12.0, commands.BucketType.user
        )
        self._spammer_count: Counter = Counter()

        # misc logging
        self._previous_websocket_events: deque = deque(maxlen=10)
        self._error_handling_cooldown: commands.CooldownMapping = commands.CooldownMapping.from_cooldown(
            1, 5, commands.BucketType.user
        )
        self.command_stats = Counter()
        self.socket_stats = Counter()
        self.global_log = logging.getLogger()

    def run(self) -> None:
        raise NotImplementedError("Please use `.start()` instead.")

    @property
    def owner(self) -> discord.User:
        return self.bot_app_info.owner

    @property
    def config(self) -> _bot_config:  # type: ignore # this actually can be used a type but I guess it's not correct practice.
        return __import__("_bot_config")

    @property
    def reminder(self) -> Reminder | None:
        return self.get_cog("Reminder")  # type: ignore # valid

    @discord.utils.cached_property
    def logging_webhook(self) -> discord.Webhook:
        return discord.Webhook.from_url(self.config.LOGGING_WEBHOOK_URL, session=self.session)

    async def on_socket_response(self, message: Any) -> None:
        """Quick override to log websocket events."""
        self._previous_websocket_events.append(message)

    async def on_ready(self) -> None:
        self.global_log.info("Kukiko got a ready event at %s", datetime.datetime.now())

    async def on_resume(self) -> None:
        self.global_log.info("Kukiko got a resume event at %s", datetime.datetime.now())

    async def on_command_error(self, ctx: Context, error: commands.CommandError) -> None:
        assert ctx.command is not None  # type checking - disable assertions
        if isinstance(error, commands.NoPrivateMessage):
            retry_period = self._error_handling_cooldown.update_rate_limit(ctx.message)
            if retry_period is None:
                return
            await ctx.send("Sorry, this command is not available in DMs.")
            return

        elif isinstance(error, commands.DisabledCommand):
            retry_period = self._error_handling_cooldown.update_rate_limit(ctx.message)
            if retry_period is None:
                return
            await ctx.send("Sorry, this command has been disabled.")
            return

        elif isinstance(error, commands.CommandInvokeError):
            origin_ = error.original
            if not isinstance(origin_, discord.HTTPException):
                print(f"In {ctx.command.qualified_name}:", file=sys.stderr)
                traceback.print_tb(origin_.__traceback__)
                print(f"{origin_.__class__.__name__}: {origin_}", file=sys.stderr)

    def _get_guild_prefixes(
        self,
        guild: discord.abc.Snowflake,
        *,
        local_: Callable[[Kukiko, discord.Message], list[str]] = _callable_prefix,
        raw: bool = False,
    ) -> list[str]:
        if raw:
            return self._prefix_data.get(guild.id, ["hey babe "])

        snowflake_proxy = discord.Object(id=0)
        snowflake_proxy.guild = guild  # type: ignore # this is actually valid, the class just has no slots or attr to override.
        return local_(self, snowflake_proxy)  # type: ignore # this is actually valid, the class just has no slots or attr to override.

    async def _set_guild_prefixes(self, guild: discord.abc.Snowflake, prefixes: list[str] | None) -> None:
        if not prefixes:
            await self._prefix_data.put(guild.id, [])
        elif len(prefixes) > 10:
            raise commands.errors.TooManyArguments("Cannot have more than 10 custom prefixes.")
        else:
            await self._prefix_data.put(guild.id, prefixes)

    async def _blacklist_add(self, object_id: int) -> None:
        await self._blacklist_data.put(object_id, True)

    async def _blacklist_remove(self, object_id: int) -> None:
        try:
            await self._blacklist_data.remove(object_id)
        except KeyError:
            pass

    @overload
    def _log_spammer(
        self, ctx: Context, message: discord.Message, retry_after: float, *, autoblock: Literal[True]
    ) -> Coroutine[None, None, discord.WebhookMessage]:
        ...

    @overload
    def _log_spammer(self, ctx: Context, message: discord.Message, retry_after: float, *, autoblock: Literal[False]) -> None:
        ...

    @overload
    def _log_spammer(self, ctx: Context, message: discord.Message, retry_after: float, *, autoblock: bool = ...) -> None:
        ...

    def _log_spammer(
        self, ctx: Context, message: discord.Message, retry_after: float, *, autoblock: bool = False
    ) -> Coroutine[None, None, discord.WebhookMessage] | None:
        guild_name = getattr(ctx.guild, "name", "No Guild (DMs)")
        guild_id = getattr(ctx.guild, "id", None)
        fmt = "User %s (ID %s) in guild %r (ID %s) is spamming. retry_after: %.2fs"
        LOGGER.warning(fmt, message.author, message.author.id, guild_name, guild_id, retry_after)
        if not autoblock:
            return

        embed = discord.Embed(title="Autoblocked Member", colour=0xDDA453)
        embed.add_field(name="User", value=f"{message.author} (ID {message.author.id})", inline=False)
        if guild_id is not None:
            embed.add_field(name="Guild Info", value=f"{guild_name} (ID {guild_id})", inline=False)
        embed.add_field(name="Channel Info", value=f"{message.channel} (ID: {message.channel.id}", inline=False)
        embed.timestamp = datetime.datetime.now(datetime.timezone.utc)

        return self.logging_webhook.send(embed=embed, wait=True)

    async def get_context(self, origin: discord.Interaction | discord.Message, /, *, cls=Context) -> Context:
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
        else:
            self._spammer_count.pop(message.author.id, None)

        try:
            await self.invoke(ctx)
        finally:
            await ctx.release()

    async def on_message(self, message: discord.Message, /) -> None:
        if message.author.bot:
            return

        await self.process_commands(message)

    async def on_message_edit(self, before: discord.Message, after: discord.Message, /) -> None:
        if after.author.id == self.owner_id:
            if not before.embeds and after.embeds:
                return

            await self.process_commands(after)

    async def on_guild_join(self, guild: discord.Guild) -> None:
        """When the bot joins a guild."""
        if guild.id in self._blacklist_data:
            await guild.leave()

    async def close(self) -> None:
        await self.md_client.logout()
        await self.pool.close()
        await super().close()
        await self.session.close()

    async def start(self) -> None:
        try:
            await super().start(token=self.config.TOKEN, reconnect=True)
        finally:
            with open("prev_events.log", "w+", encoding="utf-8") as f:
                for event in self._previous_websocket_events:
                    try:
                        last_log = json.dumps(event, ensure_ascii=True, indent=2)
                    except Exception:
                        f.write(f"{event}\n")
                    else:
                        f.write(f"{last_log}\n")

    async def setup_hook(self) -> None:
        self.mb_client = mystbin.Client(session=self.session, token=self.config.MYSTBIN_TOKEN)
        self.md_client = hondana.Client(**self.config.MANGADEX_AUTH, session=self.session)
        self.h_client = nhentaio.Client()
        self.start_time: datetime.datetime = datetime.datetime.now(datetime.timezone.utc)

        self.bot_app_info = await self.application_info()
        self.owner_id = self.bot_app_info.owner.id


async def main():
    async with Kukiko() as bot:
        pool = await asyncpg.create_pool(
            dsn=bot.config.POSTGRESQL_DSN, command_timeout=60, max_inactive_connection_lifetime=0, init=db_init
        )

        if pool is None:
            # thanks asyncpg...
            raise RuntimeError("Could not connect to database.")
        bot.pool = pool

        session = aiohttp.ClientSession()
        bot.session = session

        with SetupLogging():
            await bot.load_extension("jishaku")
            for file in pathlib.Path("extensions").glob("**/[!_]*.py"):
                ext = ".".join(file.parts).removesuffix(".py")
                try:
                    await bot.load_extension(ext)
                except Exception as error:
                    LOGGER.exception("Failed to load extension: %s\n\n%s", ext, error)

            await bot.start()


if __name__ == "__main__":
    asyncio.run(main())
