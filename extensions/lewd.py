"""
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at http://mozilla.org/MPL/2.0/.
"""

from __future__ import annotations

import logging
import random
import re
from io import BytesIO
from typing import TYPE_CHECKING, Any

import aiohttp
import asyncpg
import discord
from discord.ext import commands
from discord.http import json_or_text

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from discord.ext.commands._types import Check

    from bot import Mipha
    from utilities._types.uploader import AudioPost
    from utilities.context import Context, GuildContext

MEDIA_PATTERN = re.compile(
    r"(https?://(?P<host_url>media\.soundgasm\.net|media\d\.vocaroo\.com)(?:\/sounds|\/mp3)\/(?P<media>[a-zA-Z0-9]+)?\.?(?P<ext>m4a|mp3)?)"
)
SOUNDGASM_TITLE_PATTERN = re.compile(r"\=\"title\"\>(.*?)\<\/div\>")  # https://regex101.com/r/BJyiGM/1
SOUNDGASM_AUTHOR_PATTERN = re.compile(r"\<a href\=\"(?:(?:https?://)?soundgasm\.net\/u\/(?:.*)\")\>(.*)\<\/a>")
CONTENT_TYPE_LOOKUP = {
    "m4a": "audio/mp4",
    "mp3": "audio/mp3",
}
LOGGER = logging.getLogger(__name__)


def require_secure_keys() -> Check[Context[Lewd]]:
    def predicate(ctx: Context[Lewd]) -> bool:
        return ctx.cog._audio_enabled and ctx.cog._audio_enabled

    return commands.check(predicate)


class Lewd(commands.Cog):
    def __init__(self, bot: Mipha, /, *, uploader_token: str | None = None, audio_dsn: str | None = None) -> None:
        self.bot: Mipha = bot
        self.uploader_token: str | None = uploader_token
        self._uploader_enabled: bool = True
        self.audio_dsn: str | None = audio_dsn
        self._audio_enabled: bool = True

        if self.uploader_token is None:
            self._uploader_enabled = False
            LOGGER.warning("No token for the uploader set. Disabling all actions that require it.")
        if self.audio_dsn is None:
            self._audio_enabled = False
            LOGGER.warning("No dsn for the audio db provided.. Disabling all actions that require it.")

    async def cog_check(self, ctx: Context) -> bool:
        return await ctx.bot.is_owner(ctx.author)

    async def cog_command_error(self, ctx: Context, error: commands.CommandError) -> None:
        error = getattr(error, "original", error)

        if isinstance(error, commands.BadArgument):
            await ctx.send(str(error))
            return
        elif isinstance(error, commands.NSFWChannelRequired):
            await ctx.send(f"{error.channel} is not a horny channel. No lewdie outside lewdie channels!")
            return
        elif isinstance(error, commands.CommandOnCooldown):
            if ctx.author.id == self.bot.owner_id:
                return await ctx.reinvoke()
            await ctx.send(f"Stop being horny. You're on cooldown for {error.retry_after:.02f}s.")
            return

    async def _cache_soundgasm(
        self, url: re.Match[str], /, *, title: str | None, author: str | None
    ) -> tuple[bytes, str, int]:
        actual_url = url[0]
        ext = url["ext"]

        async with self.bot.session.get(actual_url, timeout=aiohttp.ClientTimeout(total=1800.00)) as resp:
            audio = await resp.read()

        form_data = aiohttp.FormData()
        form_data.add_field("image", audio, content_type=CONTENT_TYPE_LOOKUP[ext])
        form_data.add_field("title", title if title else "", content_type="text/plain")
        form_data.add_field("soundgasm_author", author if author else "", content_type="text/plain")

        async with self.bot.session.post(
            "https://upload.umbra-is.gay/audio",
            data=form_data,
            headers={"Authorization": self.uploader_token},
        ) as resp:
            data: AudioPost | str = await json_or_text(resp)  # type: ignore # this is weird dict narrowing

        if isinstance(data, str):
            raise ValueError(f"Returned response is not a dict:\n{data}")

        return audio, data["url"], data["size"]

    async def _cache_vocaroo(self, url: re.Match[str], /, *, media_id: str) -> tuple[bytes, str, int]:
        actual_url = url[0] + media_id
        ext: str = url.groupdict().get("ext") or "m4a"

        self.bot.log_handler.log.info("Vocaroo URL: %s", actual_url)
        headers = {
            "Accept": "audio/webm,audio/ogg,audio/wav,audio/*;q=0.9,application/ogg;q=0.7,video/*;q=0.6,*/*;q=0.5",
            "Accept-Encoding": "Identity",
            "Accept-Language": "en-GB,en,q=0.5",
            "Connection": "Keep-Alive",
            "DNT": "1",
            "Host": "media1.vocaroo.com",
            "Range": "bytes=0-",
            "Referer": "https://vocaroo.com/",
            "Sec-Fetch-Dest": "audio",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Site": "same-site",
            "Sec-GPC": "1",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/112.0",
        }
        async with self.bot.session.get(actual_url, timeout=aiohttp.ClientTimeout(total=1800.00), headers=headers) as resp:
            if not 300 > resp.status > 200:
                self.bot.log_handler.log.info("Vocaroo status code: %s", resp.status)
                raise ValueError("Non 200 response code from vocaroo.")
            audio = await resp.read()

        form_data = aiohttp.FormData()
        form_data.add_field("image", audio, content_type=CONTENT_TYPE_LOOKUP[ext])

        async with self.bot.session.post(
            "https://upload.umbra-is.gay/audio",
            data=form_data,
            headers={"Authorization": self.uploader_token},
        ) as resp:
            data: AudioPost | str = await json_or_text(resp)  # type: ignore # this is weird dict narrowing

        if isinstance(data, str):
            raise ValueError(f"Returned response is not a dict:\n{data}")

        return audio, data["url"], data["size"]

    def _audio_factory(
        self, match: re.Match[str]
    ) -> Callable[[re.Match[str], str, str | None], Coroutine[Any, Any, tuple[bytes, str, int]]]:
        if match.group("host_url") == "media.soundgasm.net":
            return self._get_soundgasm_data
        return self._get_vocaroo_data

    def _get_soundgasm_data(
        self, match: re.Match[str], data: str, _: str | None
    ) -> Coroutine[Any, Any, tuple[bytes, str, int]]:
        title_match = SOUNDGASM_TITLE_PATTERN.search(data)
        title: str | None = None
        author_match = SOUNDGASM_AUTHOR_PATTERN.search(data)
        author: str | None = None
        if title_match:
            title = re.sub(r"(\s?[\[\(].*?[\]\)]\s?)", "", title_match[1])  # https://regex101.com/r/tFLbEF/2
        if author_match:
            author = author_match[1]

        return self._cache_soundgasm(match, title=title, author=author)

    def _get_vocaroo_data(
        self, match: re.Match[str], data: str, media_id: str | None
    ) -> Coroutine[Any, Any, tuple[bytes, str, int]]:
        if not media_id:
            raise ValueError("Cannot parse Vocaroo media ID from the url.")
        return self._cache_vocaroo(match, media_id=media_id)

    @commands.command(aliases=["sg"])
    @commands.is_owner()
    @require_secure_keys()
    async def soundgasm(self, ctx: Context, *, url: str) -> None:
        """
        For archiving soundgasm links...
        """
        await ctx.typing()
        async with ctx.bot.session.get(url) as response:
            data = await response.text()

        if found_url := MEDIA_PATTERN.search(data):
            callable_ = self._audio_factory(found_url)
            media_id = None
            if found_url["host_url"] != "media.soundgasm.net":
                media_id = url.rsplit("/")[-1]
            audio, cache_url, size = await callable_(found_url, data, media_id)
        else:
            await ctx.send("Can't find the content within the main url.")
            return

        target_size = (ctx.guild and ctx.guild.filesize_limit) or 1024 * 1024 * 8
        if size >= target_size:
            await ctx.send(f"The file is too large, have the url: {cache_url}")
            return

        fmt = BytesIO(audio)
        fmt.seek(0)

        await ctx.send(file=discord.File(fmt, filename="you_horny_fuck.m4a"))

    async def _play_asmr(self, url: str, /, *, ctx: GuildContext, v_client: discord.VoiceProtocol | None) -> None:
        if not ctx.author.voice or not ctx.author.voice.channel:
            return

        v_client = v_client or await ctx.author.voice.channel.connect(cls=discord.VoiceClient)

        assert isinstance(v_client, discord.VoiceClient)

        if v_client.is_playing():
            v_client.stop()

        audio_ = discord.FFmpegPCMAudio(url)
        transformer_ = discord.PCMVolumeTransformer(audio_)
        v_client.play(transformer_)

    @commands.command()
    @commands.is_owner()
    @require_secure_keys()
    async def asmr(self, ctx: GuildContext) -> None:
        query = """
                SELECT *
                FROM audio
                TABLESAMPLE BERNOULLI (20);
                """

        conn: asyncpg.Connection = await asyncpg.connect(dsn=self.audio_dsn)

        rows = await conn.fetch(query)
        await conn.close()
        if not rows:
            return await ctx.send("No more asmr.")

        row = random.choice(rows)

        url = f"https://audio.saikoro.moe/{row['filename']}"

        await ctx.send(f"You're listening to: **{row['title']}**\nBy: **{row['soundgasm_author']}**\n{url}")

        if ctx.guild:
            await self._play_asmr(url, ctx=ctx, v_client=ctx.guild.voice_client)


async def setup(bot: Mipha) -> None:
    uploader_key: str | None = bot.config.get("uploader", {}).get("token")
    audio_dsn: str | None = bot.config["postgresql"].get("audio_dsn")

    await bot.add_cog(Lewd(bot, uploader_token=uploader_key, audio_dsn=audio_dsn))
