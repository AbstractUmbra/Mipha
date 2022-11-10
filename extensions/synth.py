from __future__ import annotations

import base64
import io
import json
import logging
import pathlib
from io import BytesIO
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands
from discord.ext import commands

from utilities._types.synth import KanaResponse, SpeakersResponse, TikTokSynth
from utilities.fuzzy import extract


if TYPE_CHECKING:
    from bot import Mipha

LOGGER: logging.Logger = logging.getLogger(__name__)

_VOICE_PATH = pathlib.Path("configs/tiktok_voices.json")
with _VOICE_PATH.open("r") as fp:
    _VOICE_DATA: list[dict[str, str]] = json.load(fp)


class BadTikTokData(Exception):
    def __init__(self, data: TikTokSynth, /) -> None:
        self._data = data
        super().__init__("TikTok Voice Synth failed.")

    @property
    def data(self) -> TikTokSynth:
        return self._data

    def clean(self) -> dict[str, Any]:
        data = self._data.copy()
        if data.get("data"):
            data["data"].pop("v_str", None)  # type: ignore

        return data  # type: ignore


class SynthCog(commands.Cog, name="Synth"):
    _tiktok_urls: set[str] = {
        "api22-normal-c-useast1a.tiktokv.com",
        "api16-normal-useast5.us.tiktokv.com",
        "api16-normal-c-alisg.tiktokv.com",
        "api19-normal-useast1a.tiktokv.com",
    }

    def __init__(self, bot: Mipha) -> None:
        self.bot: Mipha = bot
        self._engine_autocomplete: list[app_commands.Choice[int]] = []
        self._tiktok_voice_choices: list[app_commands.Choice] = [
            app_commands.Choice(name=voice["name"], value=voice["value"]) for voice in _VOICE_DATA
        ]

    async def _get_engine_choices(self) -> list[app_commands.Choice[int]]:
        if self._engine_autocomplete:
            return self._engine_autocomplete

        async with self.bot.session.get("http://127.0.0.1:50021/speakers") as resp:
            data: list[SpeakersResponse] = await resp.json()

        ret: list[app_commands.Choice[int]] = []
        for speaker in data:
            for style in speaker["styles"]:
                ret.append(
                    app_commands.Choice(name=f"[{style['id']}] {speaker['name']} ({style['name']})", value=style["id"])
                )

        ret.sort(key=lambda c: c.value)
        self._engine_autocomplete = ret
        return ret

    async def _get_kana_from_input(self, input_: str, speaker_id: int) -> KanaResponse:
        async with self.bot.session.post(
            "http://localhost:50021/audio_query",
            params={"speaker": speaker_id, "text": input_},
        ) as resp:
            data: KanaResponse = await resp.json()

        return data

    async def _get_audio_from_kana(self, kana: KanaResponse, speaker_id: int) -> BytesIO:
        async with self.bot.session.post(
            "http://localhost:50021/synthesis", params={"speaker": speaker_id}, json=kana
        ) as resp:
            data = await resp.read()

        clean = BytesIO(data)
        clean.seek(0)

        return clean

    def _tiktok_data_verification(self, data: TikTokSynth, /) -> None:
        if data["message"] == "Couldn't load speech. Try again." or data["status_code"] != 0:
            raise BadTikTokData(data)

    async def _get_tiktok_response(self, *, engine: str, text: str) -> TikTokSynth | None:
        parameters: dict[str, Any] = {"text_speaker": engine, "req_text": text, "speaker_map_type": "0", "aid": "1233"}
        headers: dict[str, str] = {
            "User-Agent": "com.zhiliaoapp.musically/2022600030 (Linux; U; Android 7.1.2; es_ES; SM-G988N; Build/NRD90M;tt-ok/3.12.13.1)",
            "Cookie": f"sessionid={self.bot.config.TIKTOK_SESSION_ID}",
        }

        for url in self._tiktok_urls:
            async with self.bot.session.post(
                f"https://{url}/media/api/text/speech/invoke/", params=parameters, headers=headers
            ) as response:
                data: TikTokSynth = await response.json()

            try:
                self._tiktok_data_verification(data)
            except BadTikTokData as error:
                LOGGER.error(
                    "TikTok synth logging.\nMessage: '%s'\nStatus Code: %d\nStatus Message: '%s'\nDict: %s",
                    text,
                    data["status_code"],
                    data["status_msg"],
                    error.clean(),
                )
                continue

            LOGGER.info(
                "TikTok synth logging.\nVoice: '%s'\nMessage: '%s'\nStatus Code: %d\nStatus Message: '%s'\nDuration: %s",
                data["data"]["speaker"],
                text,
                data["status_code"],
                data["status_msg"],
                data["data"]["duration"],
            )
            return data

    @app_commands.command(
        name="tiktok-voice", description="Generate an audio file with a given TikTok voice engine and text.", nsfw=False
    )
    @app_commands.describe(engine="Which voice engine to use", text="What do you want the voice engine to say?")
    async def tiktok_callback(self, itx: discord.Interaction, engine: str, text: str) -> None:
        await itx.response.defer(thinking=True)

        data = await self._get_tiktok_response(engine=engine, text=text)

        if not data:
            return await itx.followup.send(
                "Tiktok broke, sorry. Your input might be too long or it might just be fucked.", ephemeral=True
            )

        if data["status_code"] != 0:
            return await itx.followup.send(
                f"Sorry, your synthetic audio cannot be created due to the following reason: {data['status_msg']!r}."
            )

        vstr: str = data["data"]["v_str"]
        _padding = len(vstr) % 4
        vstr = vstr + ("=" * _padding)

        decoded = base64.b64decode(vstr)
        clean_data = io.BytesIO(decoded)
        clean_data.seek(0)

        file = discord.File(fp=clean_data, filename="tiktok_synth.mp3")

        await itx.followup.send(content=f">>> {text}", file=file)

    @tiktok_callback.autocomplete("engine")
    async def tiktok_engine_autocomplete(self, itx: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        if not current:
            return self._tiktok_voice_choices[:25]

        cleaned = extract(
            current.lower(),
            choices=[choice.name.lower() for choice in self._tiktok_voice_choices],
            limit=10,
            score_cutoff=20,
        )

        ret: list[app_commands.Choice[str]] = []
        for item, _ in cleaned:
            _x = discord.utils.get(self._tiktok_voice_choices, name=item.title())
            if _x:
                ret.append(_x)

        return ret[:25]

    @app_commands.command(name="synth", description="Synthesise some Japanese text as a sound file.", nsfw=False)
    async def synth_callback(self, itx: discord.Interaction, engine: int, text: str) -> None:
        await itx.response.defer(thinking=True)
        kana = await self._get_kana_from_input(text, engine)
        data = await self._get_audio_from_kana(kana, engine)

        file = discord.File(data, filename="synth.wav")
        await itx.followup.send(f"`{kana['kana']}`", file=file)

    @synth_callback.autocomplete("engine")
    async def synth_engine_autocomplete(self, itx: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
        choices = await self._get_engine_choices()

        if not current:
            return choices

        cleaned = extract(current, choices=[choice.name for choice in choices], limit=5, score_cutoff=20)

        ret: list[app_commands.Choice[int]] = []
        for item, _ in cleaned:
            _x = discord.utils.get(choices, name=item)
            if _x:
                ret.append(_x)

        return ret


async def setup(bot: Mipha) -> None:
    await bot.add_cog(SynthCog(bot))
