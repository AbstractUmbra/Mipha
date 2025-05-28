from __future__ import annotations

import base64
import io
import logging
import pathlib
from io import BytesIO
from typing import TYPE_CHECKING, Any, ClassVar

import discord
from discord import app_commands
from discord.ext import commands

from utilities.shared.formats import from_json
from utilities.shared.fuzzy import extract

if TYPE_CHECKING:
    from bot import Mipha
    from utilities.context import Interaction
    from utilities.shared._types.synth import KanaResponse, SpeakersResponse, TikTokSynth

LOGGER: logging.Logger = logging.getLogger(__name__)

_VOICE_PATH = pathlib.Path("configs/tiktok_voices.json")
with _VOICE_PATH.open("r") as fp:
    _VOICE_DATA: list[dict[str, str]] = from_json(fp.read())


class BadTikTokData(Exception):
    def __init__(self, data: TikTokSynth, /) -> None:
        self._data = data
        super().__init__("TikTok Voice Synth failed.")

    @property
    def data(self) -> TikTokSynth:
        return self._data

    def clean(self) -> TikTokSynth:
        data = self._data.copy()
        if data.get("data"):
            data["data"].pop("v_str", None)

        return data


class SynthCog(commands.Cog, name="Synth"):
    _tiktok_urls: ClassVar[set[str]] = {
        "api-core.tiktokv.com",
        "api-normal.tiktokv.com",
        "api16-core-c-alisg.tiktokv.com",
        "api16-core-c-useast1a.tiktokv.com",
        "api16-core-useast5.us.tiktokv.com",
        "api16-core.tiktokv.com",
        "api16-normal-c-alisg.tiktokv.com",
        "api16-normal-c-useast1a.tiktokv.com",
        "api16-normal-c-useast2a.tiktokv.com",
        "api16-normal-useast5.us.tiktokv.com",
        "api19-core-c-useast1a.tiktokv.com",
        "api19-normal-c-useast1a.tiktokv.com",
        "api22-core-c-alisg.tiktokv.com",
        "api22-normal-c-useast2a.tiktokv.com",
    }

    def __init__(self, bot: Mipha, /, *, session_id: str | None = None) -> None:
        self.bot: Mipha = bot
        self._engine_autocomplete: list[app_commands.Choice[int]] = []
        self._tiktok_voice_choices: list[app_commands.Choice[str]] = [
            app_commands.Choice(name=voice["name"], value=voice["value"]) for voice in _VOICE_DATA
        ]
        self.tiktok_session_id: str | None = session_id
        self.tiktok_context_menu_command = app_commands.ContextMenu(
            name="TiKTok Voice Synth",
            callback=self.tiktok_ctx_menu_callback,
            allowed_contexts=discord.app_commands.AppCommandContext(guild=True, dm_channel=True, private_channel=True),
            allowed_installs=discord.app_commands.AppInstallationType(guild=True, user=True),
        )
        self.bot.tree.add_command(self.tiktok_context_menu_command)

    async def cog_unload(self) -> None:
        await super().cog_unload()
        self.bot.tree.remove_command(self.tiktok_context_menu_command.name, type=self.tiktok_context_menu_command.type)

    def has_session_id(self) -> bool:
        return self.tiktok_session_id is not None

    async def _get_engine_choices(self) -> list[app_commands.Choice[int]]:
        if self._engine_autocomplete:
            return self._engine_autocomplete

        async with self.bot.session.get("http://synth:50021/speakers") as resp:
            data: list[SpeakersResponse] = await resp.json()

        ret: list[app_commands.Choice[int]] = []
        for speaker in data:
            ret.extend(
                app_commands.Choice(name=f"[{style['id']}] {speaker['name']} ({style['name']})", value=style["id"])
                for style in speaker["styles"]
            )

        ret.sort(key=lambda c: c.value)
        self._engine_autocomplete = ret
        return ret

    async def _get_kana_from_input(self, input_: str, speaker_id: int) -> KanaResponse:
        async with self.bot.session.post(
            "http://synth:50021/audio_query",
            params={"speaker": str(speaker_id), "text": input_},
        ) as resp:
            data: KanaResponse = await resp.json()

        return data

    async def _get_audio_from_kana(self, kana: KanaResponse, speaker_id: int) -> BytesIO:
        async with self.bot.session.post(
            "http://synth:50021/synthesis",
            params={"speaker": str(speaker_id)},
            json=kana,
        ) as resp:
            data = await resp.read()

        clean = BytesIO(data)
        clean.seek(0)

        return clean

    def _tiktok_data_verification(self, data: TikTokSynth, /) -> None:
        if data["message"] == "Couldn’t load speech. Try again." or data["status_code"] != 0:
            raise BadTikTokData(data)

    async def _get_tiktok_response(self, *, engine: str, text: str) -> TikTokSynth | None:
        parameters: dict[str, Any] = {"text_speaker": engine, "req_text": text, "speaker_map_type": "0", "aid": "1233"}
        headers: dict[str, str] = {
            "User-Agent": (
                "com.zhiliaoapp.musically/2022600030 "
                "(Linux; U; Android 7.1.2; es_ES; SM-G988N; Build/NRD90M;tt-ok/3.12.13.1)"
            ),
            "Cookie": f"sessionid={self.tiktok_session_id}",
        }

        for url in self._tiktok_urls:
            async with self.bot.session.post(
                f"https://{url}/media/api/text/speech/invoke/",
                params=parameters,
                headers=headers,
            ) as response:
                if response.content_type != "application/json":
                    continue
                data: TikTokSynth = await response.json()

            try:
                self._tiktok_data_verification(data)
            except BadTikTokData as error:
                LOGGER.exception(
                    "TikTok synth logging.\nURL: %r\nMessage: %r\nStatus Code: %d\nStatus Message: %r\nDict: %s",
                    url,
                    text,
                    data["status_code"],
                    data["status_msg"],
                    error.clean(),
                )
                continue

            LOGGER.info(
                "TikTok synth logging.\nURL: %r\nVoice: %r\nMessage: %r\nStatus Code: %d\nStatus Message: %r\nDuration: %s",
                url,
                data["data"]["speaker"],
                text,
                data["status_code"],
                data["status_msg"],
                data["data"]["duration"],
            )
            return data
        return None

    async def tiktok_ctx_menu_callback(self, interaction: Interaction, message: discord.Message) -> None:
        await interaction.response.defer()

        if not self.has_session_id():
            return await interaction.followup.send("Sorry, this feature is currently disabled.")

        if not message.content:
            return await interaction.followup.send("Sorry, this message has no text to synthesize.")

        data = await self._get_tiktok_response(engine="en_us_001", text=message.content)

        if not data:
            return await interaction.followup.send(
                "TikTok has broke this again. Your input might be too long or it might just be fucked.",
            )

        if data["status_code"] != 0:
            return await interaction.followup.send(
                f"Sorry, your audio cannot be created due to the following reason: {data['status_msg']!r}",
            )

        vstr = data["data"]["v_str"]
        vstr = vstr + ("=" * (len(vstr) % 4))

        decoded = base64.b64decode(vstr)
        clean_data = io.BytesIO(decoded)
        clean_data.seek(0)

        file = discord.File(fp=clean_data, filename="tiktok_synth.mp3")

        return await interaction.followup.send(content=f">>> {message.content}", file=file)

    @app_commands.command(
        name="tiktok-voice",
        description="Generate an audio file with a given TikTok voice engine and text.",
        nsfw=False,
    )
    @app_commands.describe(engine="Which voice engine to use", text="What do you want the voice engine to say?")
    async def tiktok_callback(self, interaction: Interaction, engine: str, text: str) -> None:
        await interaction.response.defer(thinking=True)

        if not self.has_session_id():
            return await interaction.followup.send("Sorry, this feature is currently disabled.")

        data = await self._get_tiktok_response(engine=engine, text=text)

        if not data:
            return await interaction.followup.send(
                "Tiktok broke, sorry. Your input might be too long or it might just be fucked.",
                ephemeral=True,
            )

        if data["status_code"] != 0:
            return await interaction.followup.send(
                f"Sorry, your synthetic audio cannot be created due to the following reason: {data['status_msg']!r}.",
            )

        vstr: str = data["data"]["v_str"]
        vstr = vstr + ("=" * (len(vstr) % 4))

        decoded = base64.b64decode(vstr)
        clean_data = io.BytesIO(decoded)
        clean_data.seek(0)

        file = discord.File(fp=clean_data, filename="tiktok_synth.mp3")

        return await interaction.followup.send(content=f">>> {text}", file=file)

    @tiktok_callback.autocomplete("engine")
    async def tiktok_engine_autocomplete(self, itx: Interaction, current: str) -> list[app_commands.Choice[str]]:
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
            engine = discord.utils.get(self._tiktok_voice_choices, name=item.title())
            if engine:
                ret.append(engine)

        return ret[:25]

    @app_commands.command(name="synth", description="Synthesise some Japanese text as a sound file.", nsfw=False)
    async def synth_callback(self, itx: Interaction, engine: int, text: str) -> None:
        await itx.response.defer(thinking=True)
        kana = await self._get_kana_from_input(text, engine)
        data = await self._get_audio_from_kana(kana, engine)

        file = discord.File(data, filename="synth.wav")
        await itx.followup.send(f"`{kana['kana']}`", file=file)

    @synth_callback.autocomplete("engine")
    async def synth_engine_autocomplete(self, itx: Interaction, current: str) -> list[app_commands.Choice[int]]:
        choices = await self._get_engine_choices()

        if not current:
            return choices[:25]

        cleaned = extract(current, choices=[choice.name for choice in choices], limit=5, score_cutoff=20)

        ret: list[app_commands.Choice[int]] = []
        for item, _ in cleaned:
            engine = discord.utils.get(choices, name=item)
            if engine:
                ret.append(engine)

        return ret[:25]


async def setup(bot: Mipha) -> None:
    session_id: str | None = bot.config.get("tokens", {}).get("tiktok")
    await bot.add_cog(SynthCog(bot, session_id=session_id))
