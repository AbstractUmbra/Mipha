from __future__ import annotations

import asyncio
import os
import pathlib
import re
from typing import TYPE_CHECKING

import discord
import yt_dlp
from discord.ext import commands


if TYPE_CHECKING:
    from bot import Kukiko

ydl = yt_dlp.YoutubeDL({"outtmpl": "buffer/%(id)s.%(ext)s", "quiet": True})

MOBILE_PATTERN = re.compile(r"(https?://(?:vm|www)\.tiktok\.com/(?:t/)?[a-zA-Z\d]+)(?:\/\?.*)?")
DESKTOP_PATTERN = re.compile(r"(https?://(?:www\.)?tiktok\.com/@(?P<user>.*)/video/(?P<video_id>\d+))\?(?:.*)")
INSTAGRAM_PATTERN = re.compile(r"(?:https?://)?(?:www\.)?instagram\.com/reel/[a-zA-Z\-\_\d]+/\?.*\=")


class TiktokCog(commands.Cog):
    def __init__(self, bot: Kukiko) -> None:
        self.bot: Kukiko = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild:
            return
        if message.guild.id != 174702278673039360:
            return

        matches = (
            MOBILE_PATTERN.findall(message.content)
            or DESKTOP_PATTERN.findall(message.content)
            or INSTAGRAM_PATTERN.findall(message.content)
        )

        if not matches:
            return

        print(f"Processing {len(matches)} detected TikToks...")

        async with message.channel.typing():
            loop = asyncio.get_running_loop()
            for idx, _url in enumerate(matches, start=1):
                if isinstance(_url, str):
                    exposed_url = _url
                else:
                    exposed_url = _url[0]

                if not exposed_url.endswith("/"):
                    exposed_url = exposed_url + "/"

                try:
                    info = await loop.run_in_executor(None, ydl.extract_info, exposed_url)
                except yt_dlp.DownloadError as error:
                    if "You need to log in" in str(error):
                        await message.channel.send("Need to log in.")
                        return
                    raise

                file_loc = pathlib.Path(f"buffer/{info['id']}.{info['ext']}")
                fixed_file_loc = pathlib.Path(f"buffer/{info['id']}_fixed.{info['ext']}")

                stat = file_loc.stat()
                if stat.st_size > message.guild.filesize_limit:
                    file_loc.unlink(missing_ok=True)
                    await message.reply(f"TikTok link #{idx} in your message exceeded the file size limit.")
                    continue

                os.system(f'ffmpeg -y -i "{file_loc}" "{fixed_file_loc}" -hide_banner -loglevel warning')
                if fixed_file_loc.stat().st_size > message.guild.filesize_limit:
                    file_loc.unlink(missing_ok=True)
                    await message.reply(f"TikTok link #{idx} in your message exceeded the file size limit.")
                    continue

                file = discord.File(str(fixed_file_loc), filename=fixed_file_loc.name)
                content = f"**Uploader**: {info['uploader']}\n\n" * (bool(info["uploader"]))
                content += f"**Description**: {info['description']}" * (bool(info["uploader"]))

                if message.mentions:
                    content = " ".join(m.mention for m in message.mentions) + "\n\n" + content

                await message.reply(content[:1000], file=file)
                if message.channel.permissions_for(message.guild.me).manage_messages and any(
                    [
                        INSTAGRAM_PATTERN.fullmatch(message.content),
                        DESKTOP_PATTERN.fullmatch(message.content),
                        MOBILE_PATTERN.fullmatch(message.content),
                    ]
                ):
                    await message.delete()
                file_loc.unlink(missing_ok=True)
                fixed_file_loc.unlink(missing_ok=True)


async def setup(bot: Kukiko) -> None:
    await bot.add_cog(TiktokCog(bot))
