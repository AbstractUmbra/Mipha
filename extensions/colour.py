from __future__ import annotations

import asyncio
import io
import re
from typing import TYPE_CHECKING

import discord
from discord.ext import commands
from jishaku.functools import executor_function
from PIL import Image

from utilities.context import Context


if TYPE_CHECKING:
    from bot import Mipha

RGB_REGEX = re.compile(r"(\|(?P<R>\d{1,3})(?:,|\s)+(?P<G>\d{1,3})(?:,|\s)+(?P<B>\d{1,3}))")
HSV_REGEX = re.compile(r"(\*(?P<H>\d{1,3}(?:\.\d)?)(?:,|\s)+(?P<S>\d{1,3}(?:\.\d)?)(?:,|\s)+(?P<V>\d{1,3}(?:\.\d)?))")
HEX_REGEX = re.compile(r"(\#[a-f0-9]{6})", re.IGNORECASE)
COLOUR_REGEX = re.compile(
    r"(?P<RGB>\|(?P<R>\d{1,3})(?:,|\s)+(?P<G>\d{1,3})(?:,?\s?)+(?P<B>\d{1,3}))|(?P<HSV>\*(?P<H>\d{1,3}(?:\.\d)?)(?:,|\s)+(?P<S>\d{1,3}(?:\.\d)?)(?:,|\s)+(?P<V>\d{1,3}(?:\.\d)?))|(?P<Hex>(?<!\<)\#[a-f0-9]{6})"
)


def hex_to_rgb(hex_: str | int) -> tuple[int, int, int]:
    if isinstance(hex_, str):
        hex_ = int(hex_.lstrip("#"), 16)

    return (hex_ >> 16, (hex_ >> 0) & 0xFF, hex_ & 0xFF)


def int_to_rgb(rgb: int) -> tuple[int, int, int]:
    return (((rgb >> 16) & 255), ((rgb >> 8) & 255), rgb & 255)


class ColourShitCog(commands.Cog):
    def __init__(self, bot: Mipha, /) -> None:
        self.bot: Mipha = bot

    @executor_function
    def _create_image(self, colour: int | discord.Colour, /) -> io.BytesIO:
        if isinstance(colour, discord.Colour):
            colour = colour.value

        ret = io.BytesIO()
        colours = int_to_rgb(colour)

        image = Image.new("RGBA", (50, 50), color=colours)
        image.save(ret, "png", optimize=True)

        ret.seek(0)
        return ret

    def _match_factory(self, match_: re.Match[str], /) -> discord.Colour:
        if match_.group("RGB"):
            return discord.Colour.from_rgb(*map(int, map(match_.group, "RGB")))
        elif match_.group("HSV"):
            return discord.Colour.from_hsv(*map(float, map(match_.group, "HSV")))
        elif match_.group("Hex"):
            return discord.Colour(int(match_.group("Hex").lstrip("#"), 16))

        raise RuntimeError("Unreachable")

    @commands.command(name="colour", aliases=["cl"])
    async def colour_info(self, ctx: Context, *, colour: discord.Colour) -> None:
        buffer = await self._create_image(colour)

        await ctx.reply(file=discord.File(buffer, filename="colour.png"))

    async def wait_for_colour_request(self, message: discord.Message) -> None:
        try:
            await message.add_reaction("\N{ARTIST PALETTE}")
        except discord.HTTPException:
            return  # blocked the bot ig

        def check(payload: discord.RawReactionActionEvent) -> bool:
            return (
                payload.message_id == message.id
                and payload.channel_id == message.channel.id
                and payload.user_id == message.author.id
                and str(payload.emoji) == "\N{ARTIST PALETTE}"
            )

        await self.bot.wait_for("raw_reaction_add", check=check, timeout=30.0)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if match := COLOUR_REGEX.search(message.content):
            try:
                await self.wait_for_colour_request(message)
            except asyncio.TimeoutError:
                return

            colour = self._match_factory(match)
            buffer = await self._create_image(colour)
            await message.reply(file=discord.File(buffer, filename="colour.png"))


async def setup(bot: Mipha) -> None:
    await bot.add_cog(ColourShitCog(bot))
