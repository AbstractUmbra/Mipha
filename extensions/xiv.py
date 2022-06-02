from __future__ import annotations

import datetime
import pathlib
import zoneinfo
from typing import TYPE_CHECKING

import discord
from discord.ext import commands, tasks

from utilities.async_config import Config


if TYPE_CHECKING:
    from bot import Kukiko


class XIV(commands.Cog):
    def __init__(self, bot: Kukiko) -> None:
        self.bot: Kukiko = bot
        self.xiv_reminder.start()
        self.character_config = Config(pathlib.Path("config/xiv_characters.json"))

    async def cog_unload(self) -> None:
        self.xiv_reminder.cancel()

    @tasks.loop(time=datetime.time(hour=15, minute=45, tzinfo=zoneinfo.ZoneInfo("Europe/London")))
    async def xiv_reminder(self) -> None:
        guild = self.bot.get_guild(174702278673039360) or await self.bot.fetch_guild(174702278673039360)
        channel = guild.get_channel(174702278673039360)
        assert isinstance(channel, discord.TextChannel)

        fmt = f"Yo <@&{970754264643293264}>, it's daily reset timer in 15 minutes."

        await channel.send(fmt, allowed_mentions=discord.AllowedMentions(roles=True))


async def setup(bot: Kukiko) -> None:
    await bot.add_cog(XIV(bot))
