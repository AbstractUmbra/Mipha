from __future__ import annotations

import asyncio
import logging
import pathlib
import re
from typing import TYPE_CHECKING, Any

import discord
import yt_dlp
from discord import app_commands
from discord.ext import commands
from jishaku.shell import ShellReader


if TYPE_CHECKING:
    from bot import Kukiko

ydl = yt_dlp.YoutubeDL({"outtmpl": "buffer/%(id)s.%(ext)s", "quiet": True})

MOBILE_PATTERN = re.compile(r"\<?(https?://(?:vt|vm|www)\.tiktok\.com/(?:t/)?[a-zA-Z\d]+)(?:\/\?.*\>?)?\>?")
DESKTOP_PATTERN = re.compile(r"\<?(https?://(?:www\.)?tiktok\.com/@(?P<user>.*)/video/(?P<video_id>\d+))(\?(?:.*))?\>?")

INSTAGRAM_PATTERN = re.compile(r"\<?(?:https?://)?(?:www\.)?instagram\.com/reel/[a-zA-Z\-\_\d]+/\?.*\=\>?")


class FilesizeLimitExceeded(Exception):
    def __init__(self, post: bool) -> None:
        self.post: bool = post
        super().__init__("The filesize limit was exceeded for this guild.")


class TiktokCog(commands.Cog):
    def __init__(self, bot: Kukiko) -> None:
        self.bot: Kukiko = bot
        self.logger: logging.Logger = logging.getLogger(__name__)
        self.tiktok_context_menu = app_commands.ContextMenu(
            name="Process TikTok link",
            callback=self.tiktok_context_menu_callback,
            guild_ids=[174702278673039360, 149998214810959872],
        )
        self.tiktok_context_menu.error(self.tiktok_context_menu_error)
        self.bot.tree.add_command(self.tiktok_context_menu)

    async def cog_unload(self) -> None:
        self.bot.tree.remove_command(self.tiktok_context_menu.name, type=self.tiktok_context_menu.type)

    async def tiktok_context_menu_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        send = (
            interaction.response.send_message
            if (not interaction.is_expired() or not interaction.response.is_done())
            else interaction.followup.send
        )

        error = getattr(error, "original", error)

        await send("Sorry but something broke. <@155863164544614402> knows and will fix it.")

    async def tiktok_context_menu_callback(self, interaction: discord.Interaction, message: discord.Message) -> None:
        await interaction.response.defer(thinking=True)

        if match := MOBILE_PATTERN.search(message.content):
            url = match[1]
        elif match := DESKTOP_PATTERN.search(message.content):
            url = match[1]
        # elif match := INSTAGRAM_PATTERN.search(message.content):
        #     url = match[0]
        else:
            await interaction.followup.send(content="I couldn't find a valid tiktok link in this message.")
            return

        loop = asyncio.get_running_loop()

        info = await self._extract_video_info(url, loop=loop)
        if not info:
            await interaction.followup.send("This message could not be parsed. Are you sure it's a valid link?")
            return

        filesize_limit = (interaction.guild and interaction.guild.filesize_limit) or 8388608
        try:
            file, content = await self._manipulate_video(info, filesize_limit=filesize_limit, loop=loop)
        except FilesizeLimitExceeded as error:
            await interaction.followup.send(content=str(error))
            return

        await interaction.followup.send(content=content, file=file)

    async def _cleanup_paths(self, *args: pathlib.Path) -> None:
        await asyncio.sleep(20)

        for path in args:
            path.unlink(missing_ok=True)

    async def _extract_video_info(self, url: str, *, loop: asyncio.AbstractEventLoop | None = None) -> dict[str, Any] | None:
        loop = loop or asyncio.get_running_loop()

        info = await loop.run_in_executor(None, ydl.extract_info, url)

        if not info:
            return

        return info

    async def _manipulate_video(
        self, info: dict[str, Any], *, filesize_limit: int, loop: asyncio.AbstractEventLoop | None = None
    ) -> tuple[discord.File, str]:
        loop = loop or asyncio.get_running_loop()
        file_loc = pathlib.Path(f"buffer/{info['id']}.{info['ext']}")
        fixed_file_loc = pathlib.Path(f"buffer/{info['id']}_fixed.{info['ext']}")

        if file_loc.stat().st_size > filesize_limit:
            file_loc.unlink(missing_ok=True)
            raise FilesizeLimitExceeded(post=False)

        with ShellReader(
            f'ffmpeg -y -i "{file_loc}" "{fixed_file_loc}" -hide_banner -loglevel warning 2>&1 >/dev/null'
        ) as reader:
            async for line in reader:
                self.logger.debug(line)

        if fixed_file_loc.stat().st_size > filesize_limit:
            file_loc.unlink(missing_ok=True)
            fixed_file_loc.unlink(missing_ok=True)
            raise FilesizeLimitExceeded(post=True)

        file = discord.File(str(fixed_file_loc), filename=fixed_file_loc.name)
        content = f"**Uploader**: {info['uploader']}\n\n" * (bool(info["uploader"]))
        content += f"**Description**: {info['description']}" * (bool(info["uploader"]))

        loop.create_task(self._cleanup_paths(file_loc, fixed_file_loc))

        return file, content

    def _pull_matches(self, matches: list[re.Match[str]]) -> list[str]:
        cleaned: list[str] = []
        for _url in matches:
            if isinstance(_url, str):
                exposed_url: str = _url
            else:
                exposed_url: str = _url[0]

            if not exposed_url.endswith("/"):
                exposed_url = exposed_url + "/"

            cleaned.append(exposed_url)

        return cleaned

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if not message.guild:
            return
        if message.guild.id not in {174702278673039360, 149998214810959872}:
            return

        matches = (
            DESKTOP_PATTERN.findall(message.content)
            or MOBILE_PATTERN.findall(message.content)
            # or INSTAGRAM_PATTERN.findall(message.content)
        )
        if not matches:
            return

        self.logger.debug("Processing %s detected TikToks...", len(matches))

        async with message.channel.typing():
            urls = self._pull_matches(matches)
            loop = asyncio.get_running_loop()
            _errors: list[int] = []
            for idx, url in enumerate(urls, start=1):
                try:
                    info = await self._extract_video_info(url, loop=loop)
                except yt_dlp.DownloadError:
                    _errors.append(idx)
                    continue

                if not info:
                    continue

                try:
                    file, content = await self._manipulate_video(info, filesize_limit=message.guild.filesize_limit)
                except FilesizeLimitExceeded:
                    await message.channel.send("The file size limit for this guild was exceeded.")
                    return

                if message.mentions:
                    content = " ".join(m.mention for m in message.mentions) + "\n\n" + content

                content = content[:1000] + f"\nRequested by: {message.author} | Replying to: {message.jump_url}"

                await message.channel.send(content, file=file)
                if message.channel.permissions_for(message.guild.me).manage_messages and any(
                    [
                        DESKTOP_PATTERN.fullmatch(message.content),
                        MOBILE_PATTERN.fullmatch(message.content),
                        # INSTAGRAM_PATTERN.fullmatch(message.content),
                    ]
                ):
                    await message.delete()


async def setup(bot: Kukiko) -> None:
    await bot.add_cog(TiktokCog(bot))
