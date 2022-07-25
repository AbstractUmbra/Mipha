from __future__ import annotations

from io import BytesIO
from typing import TYPE_CHECKING

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
