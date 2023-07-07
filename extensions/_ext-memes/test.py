from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from utilities.context import Interaction


if TYPE_CHECKING:
    from bot import Mipha

__all__ = ("FuckIt",)


class FuckIt(commands.GroupCog):
    def __init__(self, bot: Mipha) -> None:
        self.bot: Mipha = bot

    @app_commands.command()
    async def upload(self, interaction: Interaction) -> None:
        await interaction.response.send_message("Hello!")

        await interaction.followup.send(interaction.user.mention)
        await interaction.followup.send(interaction.user.mention, allowed_mentions=discord.AllowedMentions(users=True))
