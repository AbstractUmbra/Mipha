from __future__ import annotations

import logging
import pathlib
import random
import re
from typing import TYPE_CHECKING

import discord
import yarl
import yt_dlp
from discord.ext import commands

if TYPE_CHECKING:
    from bot import Mipha

LOGGER: logging.Logger = logging.getLogger(__name__)
BUFFER_PATH = pathlib.Path("./buffer/")
BUFFER_PATH.mkdir(exist_ok=True, mode=770)

ydl = yt_dlp.YoutubeDL({"outtmpl": "buffer/%(id)s.%(ext)s", "quiet": True, "logger": LOGGER})

MOBILE_PATTERN: re.Pattern[str] = re.compile(
    r"\<?(https?://(?:vt|vm|www)\.tiktok\.com/(?:t/)?[a-zA-Z\d]+\/?)(?:\/\?.*\>?)?\>?",
)
DESKTOP_PATTERN: re.Pattern[str] = re.compile(
    r"\<?(https?://(?:www\.)?tiktok\.com/@(?P<user>.*)/video/(?P<video_id>\d+))(\?(?:.*))?\>?",
)
TWITTER_PATTERN: re.Pattern[str] = re.compile(r"\<?(https?://twitter\.com/(?P<user>\w+)/status/(?P<id>\d+))\>?")
REDDIT_PATTERN: re.Pattern[str] = re.compile(r"\<?(https?://v\.redd\.it/(?P<ID>\w+))\>?")

SUBSTITUTIONS: dict[str, list[str]] = {
    "twitter.com": ["vxtwitter", "fxtwitter"],
    "x.com": ["vxtwitter", "fxtwitter"],
    "tiktok.com": ["vxtiktok"],
    "vm.tiktok.com": ["vxtiktok"],
}

GUILDS: list[discord.Object] = [
    discord.Object(id=174702278673039360),
    discord.Object(id=149998214810959872),
]

GUILD_IDS: set[int] = {guild.id for guild in GUILDS}


class MediaReposter(commands.Cog):
    def __init__(self, bot: Mipha) -> None:
        self.bot: Mipha = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if not message.guild:
            return
        if message.guild.id not in GUILD_IDS:
            return

        matches: list[re.Match[str]] = (
            list(DESKTOP_PATTERN.finditer(message.content))
            + list(MOBILE_PATTERN.finditer(message.content))
            + list(REDDIT_PATTERN.finditer(message.content))
            + list(TWITTER_PATTERN.finditer(message.content))
        )

        if not matches:
            return

        new_urls = []
        for match in matches:
            _url = yarl.URL(match[0])
            if not _url.host or not (_sub := SUBSTITUTIONS.get(_url.host, None)):
                return

            new_urls.append(_url.with_host(random.choice(_sub)))

        content = "\n".join([str(url) for url in new_urls])

        if message.mentions:
            content = " ".join(m.mention for m in message.mentions) + "\n\n" + content

        content = content[:1000] + f"\n\nReposted (correctly) from:\n{message.author} ({message.author.id})"

        await message.channel.send(content)
        if message.channel.permissions_for(message.guild.me).manage_messages and any(
            [
                DESKTOP_PATTERN.fullmatch(message.content),
                MOBILE_PATTERN.fullmatch(message.content),
                REDDIT_PATTERN.fullmatch(message.content),
                TWITTER_PATTERN.fullmatch(message.content),
            ],
        ):
            await message.delete()


async def setup(bot: Mipha) -> None:
    await bot.add_cog(MediaReposter(bot))
