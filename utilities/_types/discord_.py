from typing import TypeAlias

import discord


MessageableGuildChannel: TypeAlias = discord.TextChannel | discord.Thread | discord.VoiceChannel
