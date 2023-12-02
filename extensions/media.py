from __future__ import annotations

import asyncio
import logging
import pathlib
import re
from typing import TYPE_CHECKING, Any

import discord
import yarl
import yt_dlp
from discord import app_commands, ui
from discord.ext import commands

from utilities.cache import ExpiringCache
from utilities.time import ordinal
from utilities.ui import MiphaBaseView

if TYPE_CHECKING:
    from typing_extensions import Self

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
TWITTER_PATTERN: re.Pattern[str] = re.compile(r"\<?(https?://twitter\.com/(?P<user>\w+)/status/(?P<id>\d+))\>?")
REDDIT_PATTERN: re.Pattern[str] = re.compile(r"\<?(https?://v\.redd\.it/(?P<ID>\w+))\>?")

GUILDS: list[discord.Object] = [
    discord.Object(id=174702278673039360),
    discord.Object(id=149998214810959872),
]

GUILD_IDS: set[int] = {guild.id for guild in GUILDS}


class RepostView(MiphaBaseView):
    message: discord.InteractionMessage

    def __init__(
        self,
        urls: list[yarl.URL],
        /,
        *,
        timeout: float | None = 10,
        cog: MediaReposter,
        owner_id: int,
        target_message: discord.Message,
    ) -> None:
        self.urls: list[yarl.URL] = urls
        self.tiktok: MediaReposter = cog
        self.owner_id: int = owner_id
        self.target_message: discord.Message = target_message
        super().__init__(timeout=timeout)
        if self.tiktok is None:
            self.download_video.disabled = True

    async def on_timeout(self) -> None:
        await self.message.delete()

    @ui.button(label="Repost?", emoji="\U0001f503")
    async def repost_button(self, interaction: Interaction, button: discord.ui.Button[Self]) -> None:
        first_url = self.urls.pop(0)
        await interaction.response.send_message(content=str(first_url))
        self._disable_all_buttons()
        await self.message.edit(view=self)

        if self.urls:
            for url in self.urls:
                await interaction.channel.send(str(url))  # type: ignore # it's definitely not a stagechannel thanks

    @ui.button(label="Upload video?", emoji="\U0001f4fa")
    async def download_video(self, interaction: Interaction, button: discord.ui.Button) -> None:
        assert self.tiktok
        assert interaction.guild  # covered in the guard in message

        await interaction.response.defer()
        await self.message.delete()

        url = self.urls.pop(0)

        try:
            _info = await self.tiktok._extract_video_info(url)
        except yt_dlp.utils.DownloadError as error:
            await interaction.followup.send(
                "Sorry downloading this video broke somehow. Umbra knows don't worry.",
                ephemeral=True,
            )
            await interaction.client.tree.on_error(interaction, error)  # type: ignore
            return

        if not _info:
            await interaction.followup.send(content="I couldn't grab the video details.")
            self.repost_button.disabled = False
            await self.message.edit(view=self)
            return

        file, _ = await self.tiktok._manipulate_video(_info, filesize_limit=interaction.guild.filesize_limit)
        await self.target_message.reply(content="I downloaded the video for you:-", file=file)

    @ui.button(label="No thanks", style=discord.ButtonStyle.danger, row=2, emoji="\U0001f5d1\U0000fe0f")
    async def close_button(self, interaction: Interaction, button: discord.ui.Button[RepostView]) -> None:
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message(
                "You're not allowed to close this, only the message author can!",
                ephemeral=True,
            )
        await self.message.delete()
        self.stop()


class FilesizeLimitExceeded(Exception):
    def __init__(self, post: bool) -> None:
        self.post: bool = post
        super().__init__("The filesize limit was exceeded for this guild.")


class MediaReposter(commands.Cog):
    def __init__(self, bot: Mipha) -> None:
        self.bot: Mipha = bot
        self.media_context_menu = app_commands.ContextMenu(
            name="Process media links",
            callback=self.media_context_menu_callback,
            guild_ids=[guild.id for guild in GUILDS],
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
            return

        loop = asyncio.get_running_loop()

        url = yarl.URL(url)

        LOGGER.info("%s is trying to process the url %r", str(interaction.user), str(url))
        info = await self._extract_video_info(url, loop=loop)
        if not info:
            await interaction.followup.send(
                "This message could not be parsed. Are you sure it's a valid link?",
                ephemeral=True,
            )
            return

        filesize_limit = (interaction.guild and interaction.guild.filesize_limit) or 8388608
        try:
            file, content = await self._manipulate_video(info, filesize_limit=filesize_limit, loop=loop)
        except FilesizeLimitExceeded as error:
            await interaction.followup.send(content=str(error))
            return

        await interaction.followup.send(content=content, file=file)

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
        )

        if not matches:
            return

        LOGGER.debug("Processing %s detected TikToks...", len(matches))

        async with message.channel.typing():
            urls = self._pull_matches(matches)
            loop = asyncio.get_running_loop()
            _errors: list[int] = []
            for idx, url in enumerate(urls, start=1):
                try:
                    info = await self._extract_video_info(yarl.URL(url), loop=loop)
                except (yt_dlp.DownloadError, yt_dlp.utils.ExtractorError):
                    _errors.append(idx)
                    continue

                if not info:
                    continue

                try:
                    file, content = await self._manipulate_video(info, filesize_limit=message.guild.filesize_limit)
                except FilesizeLimitExceeded:
                    await message.channel.send("The file size limit for this guild was exceeded.")
                    return
                except asyncio.TimeoutError:
                    await message.channel.send(f"{message.author}'s video took too long to process, so I gave up.")
                    return

                if message.mentions:
                    content = " ".join(m.mention for m in message.mentions) + "\n\n" + content

                content = content[:1000] + f"\n\nRequested by:\n{message.author} ({message.author.id})"

                await message.channel.send(content, file=file)
                if _errors:
                    formatted = "I had issues downloading the "
                    formatted += ", ".join([ordinal(idx) for idx in _errors])
                    formatted += " links in your message."
                    await message.channel.send(formatted)
                if message.channel.permissions_for(message.guild.me).manage_messages and any(
                    [
                        DESKTOP_PATTERN.fullmatch(message.content),
                        MOBILE_PATTERN.fullmatch(message.content),
                        REDDIT_PATTERN.fullmatch(message.content),
                    ],
                ):
                    await message.delete()


async def setup(bot: Mipha) -> None:
    await bot.add_cog(MediaReposter(bot))
