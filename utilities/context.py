"""
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at http://mozilla.org/MPL/2.0/.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Generator, TypeVar

import asyncpg
import discord
from discord.ext import commands

from .ui import ConfirmationView


if TYPE_CHECKING:
    from aiohttp import ClientSession

    from bot import Kukiko
    from extensions._stars import StarboardConfig

__all__ = ("Context",)

T = TypeVar("T")


class _ContextDBAcquire:
    __slots__ = (
        "ctx",
        "timeout",
    )

    def __init__(self, ctx: Context, *, timeout: float | None) -> None:
        self.ctx: Context = ctx
        self.timeout: float | None = timeout

    def __await__(self) -> Generator[Any, None, asyncpg.Connection | asyncpg.Pool]:
        return self.ctx._acquire(timeout=self.timeout).__await__()

    async def __aenter__(self) -> asyncpg.Pool | asyncpg.Connection:
        await self.ctx._acquire(timeout=self.timeout)
        assert isinstance(self.ctx.db, asyncpg.Connection)
        return self.ctx.db

    async def __aexit__(self, *_) -> None:
        await self.ctx.release()


class Context(commands.Context["Kukiko"]):
    _db: asyncpg.Connection | asyncpg.Pool | None
    channel: discord.TextChannel | discord.VoiceChannel | discord.Thread | discord.DMChannel
    starboard: StarboardConfig
    bot: Kukiko
    command: commands.Command[Any, ..., Any]

    __slots__ = (
        "pool",
        "starboard",
        "_db",
    )

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.pool = self.bot.pool
        self._db: asyncpg.Connection | asyncpg.Pool | None = None

    def __repr__(self) -> str:
        return "<Context>"

    @property
    def db(self) -> asyncpg.Connection | asyncpg.Pool:
        return self._db if self._db else self.pool

    @property
    def session(self) -> ClientSession:
        return self.bot.session

    @discord.utils.cached_property
    def replied_reference(self) -> discord.MessageReference | None:
        ref = self.message.reference
        if ref and isinstance(ref.resolved, discord.Message):
            return ref.resolved.to_reference()

    async def disambiguate(self, matches: list[T], entry: Callable[[T], Any]) -> T:
        if len(matches) == 0:
            raise ValueError("No results found.")

        if len(matches) == 1:
            return matches[0]

        await self.send("There are too many matches... Which one did you mean? **Only say the number**.")
        await self.send("\n".join(f"{index}: {entry(item)}" for index, item in enumerate(matches, 1)))

        def check(m):
            return m.content.isdigit() and m.author.id == self.author.id and m.channel.id == self.channel.id

        await self.release()

        # only give them 3 tries.
        try:
            for i in range(3):
                try:
                    message = await self.bot.wait_for("message", check=check, timeout=30.0)
                except asyncio.TimeoutError:
                    raise ValueError("Took too long. Goodbye.")

                index = int(message.content)
                try:
                    return matches[index - 1]
                except Exception:
                    await self.send(f"Please give me a valid number. {2 - i} tries remaining...")

            raise ValueError("Too many tries. Goodbye.")
        finally:
            await self.acquire()

    async def prompt(
        self,
        message: str,
        *,
        timeout: float = 60.0,
        delete_after: bool = True,
        reacquire: bool = True,
        author_id: int | None = None,
    ) -> bool | None:
        """An interactive reaction confirmation dialog.

        Parameters
        -----------
        message: str
            The message to show along with the prompt.
        timeout: float
            How long to wait before returning.
        delete_after: bool
            Whether to delete the confirmation message after we're done.
        reacquire: bool
            Whether to release the database connection and then acquire it
            again when we're done.
        author_id: Optional[int]
            The member who should respond to the prompt. Defaults to the author of the
            Context's message.

        Returns
        --------
        Optional[bool]
            ``True`` if explicit confirm,
            ``False`` if explicit deny,
            ``None`` if deny due to timeout
        """

        author_id = author_id or self.author.id
        view = ConfirmationView(
            timeout=timeout,
            delete_after=delete_after,
            reacquire=reacquire,
            ctx=self,
            author_id=author_id,
        )
        view.message = await self.send(message, view=view)
        await view.wait()
        return view.value

    def tick(self, opt: bool | None, label: str | None = None) -> str:
        lookup = {
            True: "<:TickYes:735498312861351937>",
            False: "<:CrossNo:735498453181923377>",
            None: "<:QuestionMaybe:738038828928860269>",
        }
        emoji = lookup.get(opt, "❌")
        if label is not None:
            return f"{emoji}: {label}"
        return emoji

    async def _acquire(self, *, timeout: float | None = None) -> asyncpg.Connection | asyncpg.Pool:
        if self._db is None:
            self._db = await self.pool.acquire(timeout=timeout)

        return self._db

    def acquire(self) -> _ContextDBAcquire:
        return _ContextDBAcquire(self, timeout=None)

    async def release(self) -> None:
        if self._db is not None:
            await self.pool.release(self._db)
            self._db = None
