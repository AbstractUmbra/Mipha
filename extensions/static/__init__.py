from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING

import asqlite

from .cog import Static

if TYPE_CHECKING:
    from bot import Mipha

DB_PATH = pathlib.Path("/app/configs/static.db").resolve()


async def setup(bot: Mipha, /) -> None:
    pool = await asqlite.create_pool(str(DB_PATH))
    await bot.add_cog(Static(bot, pool=pool))
