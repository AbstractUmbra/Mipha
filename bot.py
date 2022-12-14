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
import secrets
import sys
import traceback
from collections import Counter, deque
from logging.handlers import RotatingFileHandler
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable, Coroutine, Iterable, Literal, overload

import aiohttp
import asyncpg
import discord
import hondana
import jishaku
import mystbin
import nhentaio
from discord import app_commands
from discord.ext import commands
from discord.utils import MISSING, _ColourFormatter as ColourFormatter, stream_supports_colour
from typing_extensions import Self

import _bot_config
from utilities.async_config import Config
from utilities.context import Context
from utilities.db import db_init
from utilities.prefix import callable_prefix as _callable_prefix


if TYPE_CHECKING:
    from discord.ext.commands._types import ContextT

    from extensions.config import Config as ConfigCog
    from extensions.reminders import Reminder

LOGGER = logging.getLogger("bot.mipha")
jishaku.Flags.HIDE = True
jishaku.Flags.RETAIN = True
jishaku.Flags.NO_UNDERSCORE = True
jishaku.Flags.NO_DM_TRACEBACK = True
INTENTS = discord.Intents(_bot_config.INTENTS)


class MiphaCommandTree(app_commands.CommandTree):
    client: Mipha

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
        if len(clean) >= 2000:
            password = secrets.token_urlsafe(16)
            paste = await self.client.mb_client.create_paste(filename="error.py", content=clean, password=password)
            e.description = (
                f"Error was too long to send in a codeblock, so I have pasted it [here]({paste.url})."
                f"\nThe password is `{password}`."
            )
        else:
            e.description = f"```py\n{clean}\n```"
        e.timestamp = datetime.datetime.now(datetime.timezone.utc)
        await self.client.logging_webhook.send(embed=e)
        await self.client.owner.send(embed=e)


class RemoveNoise(logging.Filter):
    def __init__(self) -> None:
        super().__init__(name="discord.state")

    def filter(self, record) -> bool:
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
            filename=self.logging_path / "Mipha.log", encoding="utf-8", mode="w", maxBytes=self.max_bytes, backupCount=5
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


class Mipha(commands.Bot):
    """Mipha's bot class."""

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

    def __init__(self) -> None:
        super().__init__(
            command_prefix=_callable_prefix,
            tree_cls=MiphaCommandTree,
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
        self.owner_ids = self.config.OWNER_IDS
        self.global_log = LOGGER

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

    @property
    def config_cog(self) -> ConfigCog | None:
        return self.get_cog("Config")  # type: ignore

    @discord.utils.cached_property
    def logging_webhook(self) -> discord.Webhook:
        return discord.Webhook.from_url(self.config.LOGGING_WEBHOOK_URL, session=self.session)

    async def on_socket_response(self, message: Any) -> None:
        """Quick override to log websocket events."""
        self._previous_websocket_events.append(message)

    async def on_ready(self) -> None:
        self.global_log.info("%s got a ready event at %s", self.user.name, datetime.datetime.now())

    async def on_resume(self) -> None:
        self.global_log.info("%s got a resume event at %s", self.user.name, datetime.datetime.now())

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
        local_: Callable[[Mipha, discord.Message], list[str]] = _callable_prefix,
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

    async def get_or_fetch_member(self, guild: discord.Guild, member_id: int) -> discord.Member | None:
        member = guild.get_member(member_id)
        if member is not None:
            return member

        shard: discord.ShardInfo = self.get_shard(guild.shard_id)  # type: ignore  # will never be None
        if shard.is_ws_ratelimited():
            try:
                member = await guild.fetch_member(member_id)
            except discord.HTTPException:
                return None
            else:
                return member

        members = await guild.query_members(limit=1, user_ids=[member_id], cache=True)
        if not members:
            return None
        return members[0]

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
            shard: discord.ShardInfo = self.get_shard(guild.shard_id)  # type: ignore  # will never be None
            if shard.is_ws_ratelimited():
                try:
                    member = await guild.fetch_member(needs_resolution[0])
                except discord.HTTPException:
                    pass
                else:
                    yield member
            else:
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
    async def get_context(self, origin: discord.Interaction | discord.Message, /) -> Context:
        ...

    @overload
    async def get_context(self, origin: discord.Interaction | discord.Message, /, *, cls: type[ContextT]) -> ContextT:
        ...

    async def get_context(
        self, origin: discord.Interaction | discord.Message, /, *, cls: type[ContextT] = MISSING
    ) -> ContextT:
        if cls is MISSING:
            cls = Context  # type: ignore
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

        if can_edit:
            await self.process_commands(after)

    async def on_guild_join(self, guild: discord.Guild) -> None:
        """When the bot joins a guild."""
        if guild.id in self._blacklist_data:
            await guild.leave()

    async def close(self) -> None:
        await self.md_client.logout()
        await self.pool.close()
        await super().close()

    async def start(self) -> None:
        try:
            await super().start(token=self.config.TOKEN, reconnect=True)
        finally:
            path = pathlib.Path("logs/prev_events.log")
            with path.open("w+", encoding="utf-8") as f:
                for event in self._previous_websocket_events:
                    try:
                        last_log = json.dumps(event, ensure_ascii=True, indent=2)
                    except Exception:
                        f.write(f"{event}\n")
                    else:
                        f.write(f"{last_log}\n")

    async def setup_hook(self) -> None:
        self.start_time: datetime.datetime = datetime.datetime.now(datetime.timezone.utc)

        self.bot_app_info = await self.application_info()
        # self.owner_id = self.bot_app_info.owner.id
        self.owner_ids = self.config.OWNER_IDS


async def main() -> None:
    async with Mipha() as bot, aiohttp.ClientSession() as session, asyncpg.create_pool(
        dsn=bot.config.POSTGRESQL_DSN, command_timeout=60, max_inactive_connection_lifetime=0, init=db_init
    ) as pool:
        if pool is None:
            # thanks asyncpg...
            raise RuntimeError("Could not connect to database.")

        bot.pool = pool

        bot.session = session

        bot.mb_client = mystbin.Client(session=session, token=bot.config.MYSTBIN_TOKEN)
        bot.md_client = hondana.Client(**bot.config.MANGADEX_AUTH, session=session)
        bot.h_client = nhentaio.Client()

        with SetupLogging():
            await bot.load_extension("jishaku")
            path = pathlib.Path("extensions")
            for file in path.rglob("[!_]*.py"):
                if (file.is_dir() and file.name.startswith("ext-")) or (
                    file.parent.is_dir() and file.parent.name.startswith("ext-")
                ):
                    continue
                ext = ".".join(file.parts).removesuffix(".py")
                try:
                    await bot.load_extension(ext)
                    LOGGER.info("Loaded extension: %s", ext)
                except Exception as error:
                    LOGGER.exception("Failed to load extension: %s\n\n%s", ext, error)
            for directory in path.rglob("ext-*"):
                if not directory.is_dir():
                    return
                module = ".".join(directory.parts)
                try:
                    await bot.load_extension(module)
                    LOGGER.info("Loaded module extension: %s", module)
                except Exception as error:
                    LOGGER.exception("Failed to load module extension: %s\n\n%s", module, error)

            await bot.start()


if __name__ == "__main__":
    asyncio.run(main())
