from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING

from discord.utils import _from_json  # noqa: PLC2701 # this is okay

if TYPE_CHECKING:
    from bot import Mipha

    from ._types.config import Details

LOADABLE: bool = True
try:
    import mcstatus as _
    import rcon as __  # pyright: ignore[reportUnusedImport]
except ModuleNotFoundError:
    LOADABLE = False


async def setup(bot: Mipha) -> None:
    if not LOADABLE:
        return

    config_path = pathlib.Path("configs/minecraft.json")
    if not config_path.exists():
        return

    data: dict[str, Details] = _from_json(config_path.read_text(encoding="utf-8"))

    from .cog import Minecraft  # noqa: PLC0415

    await bot.add_cog(Minecraft(bot, config=data))
