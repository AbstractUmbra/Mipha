from __future__ import annotations
import base64

from io import BytesIO
import io
from typing import TYPE_CHECKING, Any


import discord
from discord import app_commands
from discord.ext import commands

from utilities._types.synth import KanaResponse, SpeakersResponse
from utilities.fuzzy import extract


if TYPE_CHECKING:
    from bot import Kukiko


class SynthCog(commands.Cog, name="Synth"):
    def __init__(self, bot: Kukiko) -> None:
        self.bot: Kukiko = bot
        self._engine_autocomplete: list[app_commands.Choice[int]] = []
        self._tiktok_voice_choices: list[app_commands.Choice[str]] = [
            app_commands.Choice(name="Default", value="en_us_001"),
            app_commands.Choice(name="Ghost Face", value="en_us_ghostface"),
            app_commands.Choice(name="Chewbacca", value="en_us_chewbacca"),
            app_commands.Choice(name="C3PO", value="en_us_c3po"),
            app_commands.Choice(name="Stitch", value="en_us_stitch"),
            app_commands.Choice(name="Stormtrooper", value="en_us_stormtrooper"),
            app_commands.Choice(name="Rocket", value="en_us_rocket"),
            app_commands.Choice(name="Australian Female", value="en_au_001"),
            app_commands.Choice(name="Austrlian Male 1", value="en_au_002"),
            app_commands.Choice(name="Australian Male 2", value="en_uk_001"),
            app_commands.Choice(name="Australian Male 3", value="en_uk_003"),
            app_commands.Choice(name="American Female 1", value="en_us_001"),
            app_commands.Choice(name="American Female 2", value="en_us_002"),
            app_commands.Choice(name="American Male 1", value="en_us_006"),
            app_commands.Choice(name="American Male 2", value="en_us_007"),
            app_commands.Choice(name="American Male 3", value="en_us_009"),
            app_commands.Choice(name="American Male 4", value="en_us_010"),
            app_commands.Choice(name="French Male 1", value="fr_001"),
            app_commands.Choice(name="French Male 2", value="fr_002"),
            app_commands.Choice(name="German Female", value="de_001"),
            app_commands.Choice(name="German Male", value="de_002"),
            app_commands.Choice(name="Spanish Male", value="es_002"),
            app_commands.Choice(name="Spanish (Mexican) Male", value="es_mx_002"),
            app_commands.Choice(name="Brazilian Female 1", value="br_001"),
            app_commands.Choice(name="Brazilian Female 2", value="br_003"),
            app_commands.Choice(name="Brazilian Female 3", value="br_004"),
            app_commands.Choice(name="Brazilian Male", value="br_005"),
            app_commands.Choice(name="Indonesian Female", value="id_001"),
            app_commands.Choice(name="Japanese Female 1", value="jp_001"),
            app_commands.Choice(name="Japanese Female 2", value="jp_003"),
            app_commands.Choice(name="Japanese Female 3", value="jp_005"),
            app_commands.Choice(name="Japanese Male", value="jp_006"),
            app_commands.Choice(name="Korean Male 1", value="kr_002"),
            app_commands.Choice(name="Korean Female", value="kr_003"),
            app_commands.Choice(name="Korean Male 2", value="kr_004"),
            app_commands.Choice(name="Alto", value="en_female_f08_salut_damour"),
            app_commands.Choice(name="Tenor", value="en_male_m03_lobby"),
            app_commands.Choice(name="Warmy Breeze", value="en_female_f08_warmy_breeze"),
            app_commands.Choice(name="Sunshine Soon", value="en_male_m03_sunshine_soon"),
            app_commands.Choice(name="Narrator", value="en_male_narration"),
            app_commands.Choice(name="Wacky", value="en_male_funny"),
            app_commands.Choice(name="Peaceful", value="en_female_emotional"),
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

    async def _get_tiktok_response(self, *, engine: str, text: str) -> dict[str, Any]:
        parameters: dict[str, Any] = {"text_speaker": engine, "req_text": text, "speaker_map_type": "0"}

        async with self.bot.session.post(
            "https://api16-normal-useast5.us.tiktokv.com/media/api/text/speech/invoke/", params=parameters
        ) as response:
            data = await response.json()

        return data

    @app_commands.command(
        name="tiktok-voice", description="Generate an audio file with a given TikTok voice engine and text.", nsfw=False
    )
    async def tiktok_callback(self, itx: discord.Interaction, engine: str, text: str) -> None:
        await itx.response.defer(thinking=True)

        data = await self._get_tiktok_response(engine=engine, text=text)

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
            return self._tiktok_voice_choices[:20]

        cleaned = extract(
            current.lower(), choices=[choice.name.lower() for choice in self._tiktok_voice_choices], limit=5, score_cutoff=20
        )

        ret: list[app_commands.Choice[str]] = []
        for item, _ in cleaned:
            _x = discord.utils.get(self._tiktok_voice_choices, name=item)
            if _x:
                ret.append(_x)

        return ret

    @app_commands.command(name="synth", description="Synthesise some Japanese text as a sound file.", nsfw=False)
    async def synth_callback(self, itx: discord.Interaction, engine: int, text: str) -> None:
        await itx.response.defer(thinking=True)
        kana = await self._get_kana_from_input(text, engine)
        data = await self._get_audio_from_kana(kana, engine)

        file = discord.File(data, filename="synth.wav")
        await itx.followup.send(f"`{kana['kana']}`", file=file)

    @synth_callback.autocomplete("engine")
    async def synth_engine_autocomplete(self, itx: discord.Interaction, current: int) -> list[app_commands.Choice[int]]:
        choices = await self._get_engine_choices()

        if not current:
            return choices

        cleaned = extract(str(current), choices=[choice.name for choice in choices], limit=5, score_cutoff=20)

        ret: list[app_commands.Choice[int]] = []
        for item, _ in cleaned:
            _x = discord.utils.get(choices, name=item)
            if _x:
                ret.append(_x)

        return ret


async def setup(bot: Kukiko) -> None:
    await bot.add_cog(SynthCog(bot))
