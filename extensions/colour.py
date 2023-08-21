from __future__ import annotations

import io
import re
from typing import TYPE_CHECKING

import discord
from discord.ext import commands
from jishaku.functools import executor_function
from PIL import Image

if TYPE_CHECKING:
    from bot import Mipha
    from utilities.context import Context

COLOUR_REGEX = re.compile(
    (
        r"(?P<RGB>(?P<R>\d{1,3})(?:,|\s)+(?P<G>\d{1,3})(?:,?\s?)+(?P<B>\d{1,3}))"
        r"|(?P<HSV>(?P<H>\d{1,3}(?:\.\d)?)(?:,|\s)+(?P<S>\d{1,3}(?:\.\d)?)(?:,|\s)+(?P<V>\d{1,3}(?:\.\d)?))"
        r"|(?P<Hex>(?<!\<)\#(?:[a-f0-9]{6}|[a-f0-9]{3}\b))"
    ),
    re.IGNORECASE,
)


def hex_to_rgb(hex_: str | int) -> tuple[int, int, int]:
    if isinstance(hex_, str):
        hex_ = int(hex_.lstrip("#"), 16)

    return (hex_ >> 16, (hex_ >> 0) & 0xFF, hex_ & 0xFF)


class ColourShitCog(commands.Cog):
    def __init__(self, bot: Mipha, /) -> None:
        self.bot: Mipha = bot

    @executor_function
    def _create_image(self, colour: int | discord.Colour, /) -> io.BytesIO:
        if isinstance(colour, int):
            colour = discord.Colour(colour)

        ret = io.BytesIO()
        colours = colour.to_rgb()

        image = Image.new("RGBA", (50, 50), color=colours)
        image.save(ret, "png", optimize=True)

        ret.seek(0)
        return ret

    def _match_factory(self, match_: re.Match[str], /) -> discord.Colour:
        try:
            return discord.Colour.from_str(match_[0])
        except ValueError:
            pass

        if match_.group("RGB"):
            return discord.Colour.from_rgb(*map(int, map(match_.group, "RGB")))
        elif match_.group("HSV"):
            return discord.Colour.from_hsv(*map(float, map(match_.group, "HSV")))
        elif match_.group("Hex"):
            group = match_["Hex"]
            if len(group) == 4:
                group = "#" + "".join(character * 2 for character in group.removeprefix("#"))

            return discord.Colour(int(group.lstrip("#"), 16))

        raise RuntimeError("Unreachable")

    @commands.hybrid_command(name="colour")
    async def colour_command(self, ctx: Context, *, colour_input: str | None = None) -> None:
        """Shows a panel of colour from the input given. Accepts Hex, RGB and HSV codes."""
        input_ = colour_input or (
            ctx.replied_reference and ctx.replied_reference.cached_message and ctx.replied_reference.cached_message.content
        )
        if not input_:
            await ctx.send("Sorry I don't see a colour code anywhere?")
            return

        if match := COLOUR_REGEX.search(input_):
            colour = self._match_factory(match)
            buffer = await self._create_image(colour)
            await ctx.reply(file=discord.File(buffer, filename="colour.png"))


async def setup(bot: Mipha) -> None:
    await bot.add_cog(ColourShitCog(bot))
