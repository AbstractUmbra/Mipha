from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from discord import Message
from discord.ext import commands


if TYPE_CHECKING:
    from bot import Kukiko


def callable_prefix(bot: Kukiko, message: Message, /) -> list[str]:
    if message.guild is None:
        return commands.when_mentioned_or("hey babe ")(bot, message)

    guild_prefixes: Optional[list[str]] = bot._prefix_data.get(str(message.guild.id))
    if guild_prefixes is None:
        guild_prefixes = ["hey babe "]

    return commands.when_mentioned_or(*guild_prefixes)(bot, message)
