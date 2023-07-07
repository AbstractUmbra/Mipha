from __future__ import annotations

from typing import TYPE_CHECKING

from discord.ext import commands


if TYPE_CHECKING:
    from bot import Mipha


class CoupCog(commands.GroupCog, name="coup"):
    def __init__(self, bot: Mipha, /) -> None:
        self.bot: Mipha = bot
        super().__init__()
