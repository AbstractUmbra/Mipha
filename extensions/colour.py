from __future__ import annotations

import io
from typing import TYPE_CHECKING

import discord
from discord.ext import commands
from jishaku.functools import executor_function
from PIL import Image

from utilities.shared.converters import ColourConverter

if TYPE_CHECKING:
    from bot import Mipha
    from utilities.context import Context


class Colour(commands.Cog):
    """An extension to do things with colours!"""

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

    @commands.hybrid_command(name="colour")
    async def colour_command(
        self,
        ctx: Context,
        *,
        colour_input: discord.Colour | None = commands.param(converter=ColourConverter, default=None),  # ruff: ignore[function-call-in-default-argument]
    ) -> None:
        """Shows a panel of colour from the input given. Accepts Hex, RGB and HSV codes."""
        resolved = (
            ctx.replied_reference and ctx.replied_reference.cached_message and ctx.replied_reference.cached_message.content
        )
        if not colour_input or not resolved:
            await ctx.send("Sorry I don't see a colour code anywhere?")
            return

        if resolved:
            resolved_colour = await ColourConverter().convert(ctx, resolved)

        buffer = await self._create_image(colour_input or resolved_colour)
        await ctx.reply(file=discord.File(buffer, filename="colour.png"))


async def setup(bot: Mipha) -> None:
    await bot.add_cog(Colour(bot))
