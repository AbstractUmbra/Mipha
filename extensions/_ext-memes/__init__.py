from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from .test import FuckIt as FuckIt


if TYPE_CHECKING:
    from bot import Mipha


async def setup(bot: Mipha) -> None:
    await bot.add_cog(FuckIt(bot), guild=discord.Object(id=705500489248145459))
