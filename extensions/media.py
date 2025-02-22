from __future__ import annotations

import asyncio
import logging
import pathlib
import random
import re
from typing import TYPE_CHECKING, Any, TypedDict

import discord
import yarl
import yt_dlp
from discord import app_commands
from discord.ext import commands

from utilities.shared.async_config import Config
from utilities.shared.cache import ExpiringCache
from utilities.shared.ui import SelfDeleteView

if TYPE_CHECKING:
    from bot import Mipha
    from utilities.context import Interaction

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
    "twitter.com": {"repost_urls": ["fixupx.com", "girlcockx.com"], "remove_query": True},
    "x.com": {"repost_urls": ["fixupx.com", "girlcockx.com"], "remove_query": True},
    "tiktok.com": {"repost_urls": ["vxtiktok.com"], "remove_query": False},
    "www.tiktok.com": {"repost_urls": ["vxtiktok.com"], "remove_query": False},
    "vm.tiktok.com": {"repost_urls": ["vxtiktok.com"], "remove_query": False},
    "instagram.com": {"repost_urls": ["ddinstagram.com"], "remove_query": True},
    "www.instagram.com": {"repost_urls": ["ddinstagram.com"], "remove_query": True},
}

AUTO_REPOST_GUILDS: list[discord.Object] = [
    discord.Object(id=774561547930304536, type=discord.Guild),
    discord.Object(id=174702278673039360, type=discord.Guild),
    discord.Object(id=149998214810959872, type=discord.Guild),
]
AUTO_REPOST_GUILD_IDS: set[int] = {guild.id for guild in AUTO_REPOST_GUILDS}


class SubstitutionData(TypedDict):
    repost_urls: list[str]
    remove_query: bool


class MediaConfig(TypedDict):
    allowed_roles: list[int]
    allowed_members: list[int]


class FilesizeLimitExceeded(Exception):
    def __init__(self, *, post: bool) -> None:
        self.post: bool = post
        super().__init__("The filesize limit was exceeded for this guild.")


class MediaReposter(commands.Cog):
    def __init__(self, bot: Mipha, config: Config[MediaConfig]) -> None:
        self.bot: Mipha = bot
        self.config: Config[MediaConfig] = config
        self.media_context_menu = app_commands.ContextMenu(
            name="Process media links",
            callback=self.media_context_menu_callback,
        )

        self.media_context_menu.error(self.media_context_menu_error)
        self.bot.tree.add_command(self.media_context_menu)
        self.task_mapping = ExpiringCache[asyncio.Task[None]](seconds=20)

    async def cog_unload(self) -> None:
        self.bot.tree.remove_command(self.media_context_menu.name, type=self.media_context_menu.type)

    async def media_context_menu_error(self, interaction: Interaction, error: app_commands.AppCommandError) -> None:
        send = interaction.response.send_message if not interaction.response.is_done() else interaction.followup.send

        error = getattr(error, "original", error)

        await send("Sorry but something broke. <@155863164544614402> knows and will fix it.")

    async def media_context_menu_callback(self, interaction: Interaction, message: discord.Message) -> None:
        await interaction.response.defer(thinking=True)

        if not self._check_author(interaction.user):
            return await interaction.followup.send("Sorry, you don't meet the role gate to use this.", ephemeral=True)

        if (
            match := MOBILE_PATTERN.search(message.content)
            or DESKTOP_PATTERN.search(message.content)
            or TWITTER_PATTERN.search(message.content)
        ):
            url = match[1]
        elif match := REDDIT_PATTERN.search(message.content):
            url = match[0]
        else:
            await interaction.followup.send(content="I couldn't find a valid tiktok link in this message.", ephemeral=True)
            return None

        loop = asyncio.get_running_loop()

        url = yarl.URL(url)

        LOGGER.info("%s is trying to process the url %r", str(interaction.user), str(url))
        info = await self._extract_video_info(url, loop=loop)
        if not info:
            await interaction.followup.send(
                "This message could not be parsed. Are you sure it's a valid link?",
                ephemeral=True,
            )
            return None

        filesize_limit = (interaction.guild and interaction.guild.filesize_limit) or 8388608
        try:
            file, content = await self._manipulate_video(info, filesize_limit=filesize_limit, loop=loop)
        except FilesizeLimitExceeded as error:
            await interaction.followup.send(content=str(error))
            return None

        return await interaction.followup.send(content=content, file=file)

    async def _cleanup_paths(self, *args: pathlib.Path) -> None:
        await asyncio.sleep(60)

        for path in args:
            path.unlink(missing_ok=True)

    async def _extract_video_info(
        self,
        url: yarl.URL,
        *,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> dict[str, Any] | None:
        LOGGER.info("Extracting URL: %r", url)
        loop = loop or asyncio.get_running_loop()

        info = await loop.run_in_executor(None, ydl.extract_info, str(url))

        if not info:
            return None

        return info

    async def _manipulate_video(
        self,
        info: dict[str, Any],
        *,
        filesize_limit: int,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> tuple[discord.File, str]:
        loop = loop or asyncio.get_running_loop()
        file_loc = pathlib.Path(f"buffer/{info['id']}.{info['ext']}")
        fixed_file_loc = pathlib.Path(f"buffer/{info['id']}_fixed.{info['ext']}")

        if file_loc.stat().st_size > filesize_limit:
            file_loc.unlink(missing_ok=True)
            raise FilesizeLimitExceeded(post=False)

        proc = await asyncio.subprocess.create_subprocess_shell(
            f'ffmpeg -y -i "{file_loc}" "{fixed_file_loc}" -hide_banner -loglevel warning',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        _, _ = await proc.communicate()

        if fixed_file_loc.stat().st_size > filesize_limit:
            file_loc.unlink(missing_ok=True)
            fixed_file_loc.unlink(missing_ok=True)
            raise FilesizeLimitExceeded(post=True)

        file = discord.File(str(fixed_file_loc), filename=fixed_file_loc.name)
        content = f"**Uploader**: {info['uploader']}\n\n" * (bool(info["uploader"]))
        content += f"**Description**: {info.get('description', '')}" * (bool(info["uploader"]))

        if file_loc.name in self.task_mapping:
            self.task_mapping[file_loc.name].cancel()

        task = loop.create_task(self._cleanup_paths(file_loc, fixed_file_loc))
        self.task_mapping[file_loc.name] = task

        return file, content

    def _pull_matches(self, matches: list[re.Match[str]]) -> list[str]:
        cleaned: list[str] = []
        for _url in matches:
            exposed_url: str = _url[1]

            if not exposed_url.endswith("/"):
                exposed_url = exposed_url + "/"

            cleaned.append(exposed_url)

        return cleaned

    def _check_author(self, author: discord.Member | discord.User) -> bool:
        if isinstance(author, discord.User):
            # dms?
            return True

        config_entry = self.config.get(author.guild.id)
        if not config_entry:
            return True

        return any(author.get_role(r) for r in config_entry["allowed_roles"]) or author.id in config_entry["allowed_members"]

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if not message.guild:
            return
        if message.guild.id not in AUTO_REPOST_GUILD_IDS:
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
            url = yarl.URL(match[0])
            if not url.host or not (_sub := SUBSTITUTIONS.get(url.host, None)):
                return

            new_url = url.with_host(random.choice(_sub["repost_urls"]))  # noqa: S311 # not crypto
            if _sub["remove_query"] is True:
                new_url = new_url.with_query(None)

            new_urls.append(new_url)

        content = "\n".join([str(url) for url in new_urls])

        if message.mentions:
            content = " ".join(m.mention for m in message.mentions) + "\n\n" + content

        content = content[:1000] + f"\n\nReposted (correctly) from:\n{message.author} ({message.author.id})"

        view = SelfDeleteView(author_id=message.author.id)
        view.message = await message.channel.send(content, view=view)
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
    await bot.add_cog(MediaReposter(bot, config), guilds=[discord.Object(id=x) for x in AUTO_REPOST_GUILD_IDS])
