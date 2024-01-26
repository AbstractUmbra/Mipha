from __future__ import annotations

import logging
import pathlib
import random
import re
from typing import TYPE_CHECKING, TypedDict

import discord
import yarl
import yt_dlp
from discord.ext import commands

from utilities.shared.async_config import Config

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
TWITTER_PATTERN: re.Pattern[str] = re.compile(r"\<?(https?://(twitter|x)\.com/(?P<user>\w+)/status/(?P<id>\d+))\>?")
REDDIT_PATTERN: re.Pattern[str] = re.compile(r"\<?(https?://v\.redd\.it/(?P<ID>\w+))\>?")
INSTAGRAM_PATTERN: re.Pattern[str] = re.compile(r"\<?(https?://(?:www\.)instagram\.com/reel/(?P<id>[a-zA-Z0-9]+)\/?)\>?")

SUBSTITUTIONS: dict[str, SubstitutionData] = {
    # "twitter.com": {"repost_urls": ["vxtwitter.com", "fxtwitter.com"], "remove_query": True},
    # "x.com": {"repost_urls": ["vxtwitter.com", "fxtwitter.com"], "remove_query": True},
    "tiktok.com": {"repost_urls": ["vm.tiktxk.com"], "remove_query": False},
    "vm.tiktok.com": {"repost_urls": ["vm.tiktxk.com"], "remove_query": False},
    "instagram.com": {"repost_urls": ["ddinstagram.com"], "remove_query": True},
    "www.instagram.com": {"repost_urls": ["ddinstagram.com"], "remove_query": True},
}

GUILDS: list[discord.Object] = [
    discord.Object(id=174702278673039360),
    discord.Object(id=149998214810959872),
]

GUILD_IDS: set[int] = {guild.id for guild in GUILDS}


class SubstitutionData(TypedDict):
    repost_urls: list[str]
    remove_query: bool


class MediaConfig(TypedDict):
    allowed_roles: list[int]
    allowed_members: list[int]


class MediaReposter(commands.Cog):
    def __init__(self, bot: Mipha, config: Config[MediaConfig]) -> None:
        self.bot: Mipha = bot
        self.config: Config[MediaConfig] = config

    def _check_author(self, author: discord.Member) -> bool:
        config_entry = self.config.get(author.guild.id)
        if not config_entry:
            return True

        return any(author.get_role(r) for r in config_entry["allowed_roles"]) or author.id in config_entry["allowed_members"]

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if not message.guild:
            return
        if message.guild.id not in GUILD_IDS:
            return
        if message.webhook_id:
            return

        matches: list[re.Match[str]] = (
            list(DESKTOP_PATTERN.finditer(message.content))
            + list(MOBILE_PATTERN.finditer(message.content))
            + list(REDDIT_PATTERN.finditer(message.content))
            + list(TWITTER_PATTERN.finditer(message.content))
            + list(INSTAGRAM_PATTERN.finditer(message.content))
        )

        if not matches:
            return

        assert isinstance(message.author, discord.Member)  # guarded in previous if
        if not self._check_author(message.author):
            return

        new_urls = []
        for match in matches:
            _url = yarl.URL(match[0])
            if not _url.host or not (_sub := SUBSTITUTIONS.get(_url.host, None)):
                return

            new_url = _url.with_host(random.choice(_sub["repost_urls"]))
            if _sub["remove_query"] is True:
                new_url = new_url.with_query(None)

            new_urls.append(new_url)

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
                INSTAGRAM_PATTERN.fullmatch(message.content),
            ],
        ):
            await message.delete()


async def setup(bot: Mipha) -> None:
    config_path = pathlib.Path("configs/media.json")
    config = Config(config_path)
    await bot.add_cog(MediaReposter(bot, config))
