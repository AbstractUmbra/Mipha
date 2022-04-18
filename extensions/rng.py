"""
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at http://mozilla.org/MPL/2.0/.
"""

from __future__ import annotations

import random
import re
from collections import Counter
from typing import TYPE_CHECKING, TypedDict

import discord
from discord.ext import commands

from utilities.context import Context
from utilities.formats import plural, to_codeblock


if TYPE_CHECKING:
    from bot import Kukiko

    from .tags import Tags

DICE_RE = re.compile(r"^(?P<rolls>\d+)[dD](?P<die>\d+)$")


class DiceType(TypedDict):
    rolls: int
    die: int
    totals: list[int]


class DiceRoll(commands.Converter[DiceType]):
    async def convert(self, _: commands.Context, argument: str) -> DiceType:
        search = DICE_RE.fullmatch(argument)
        if not search:
            raise commands.BadArgument("Dice roll doesn't seem valid, please use it in the format of `2d20`.")

        search = search.groupdict()
        rolls = max(min(int(search["rolls"]), 15), 1)
        die = max(min(int(search["die"]), 1000), 2)

        totals = [random.randint(1, die) for _ in range(rolls)]

        return {"rolls": rolls, "die": die, "totals": totals}


class RNG(commands.Cog):
    """Utilities that provide pseudo-RNG."""

    def __init__(self, bot: Kukiko) -> None:
        self.bot = bot

    @commands.group()
    async def random(self, ctx: Context) -> None:
        """Displays a random thing you request."""
        if ctx.invoked_subcommand is None:
            await ctx.send(f"Incorrect random subcommand passed. Try {ctx.prefix}help random")

    @random.command()
    async def tag(self, ctx: Context) -> None:
        """Displays a random tag.

        A tag showing up in this does not get its usage count increased.
        """
        assert ctx.guild is not None

        tags: Tags = self.bot.get_cog("Tags")  # type: ignore # yeah idk???

        if tags is None:
            await ctx.send("Tag commands currently disabled.")
            return

        tag = await tags.get_random_tag(ctx.guild, connection=ctx.db)  # type: ignore # yeah idk???
        if tag is None:
            await ctx.send("This server has no tags.")
            return

        await ctx.send(f'Random tag found: {tag["name"]}\n{tag["content"]}')

    @random.command()
    async def number(self, ctx: Context, minimum: int = 0, maximum: int = 100) -> None:
        """Displays a random number within an optional range.

        The minimum must be smaller than the maximum and the maximum number
        accepted is 1000.
        """

        maximum = min(maximum, 1000)
        if minimum >= maximum:
            await ctx.send("Maximum is smaller than minimum.")
            return

        await ctx.send(str(random.randint(minimum, maximum)))

    @commands.command()
    async def choose(self, ctx: Context, *choices: str if TYPE_CHECKING else commands.clean_content) -> None:
        """Chooses between multiple choices.

        To denote multiple choices, you should use double quotes.
        """
        if len(choices) < 2:
            await ctx.send("Not enough choices to pick from.")
            return

        await ctx.send(random.choice(choices))

    @commands.command()
    async def choosebestof(
        self, ctx: Context, times: int | None, *choices: str if TYPE_CHECKING else commands.clean_content
    ) -> None:
        """Chooses between multiple choices N times.

        To denote multiple choices, you should use double quotes.

        You can only choose up to 10001 times and only the top 10 results are shown.
        """

        if len(choices) < 2:
            await ctx.send("Not enough choices to pick from.")
            return

        if times is None:
            times = (len(choices) ** 2) + 1

        times = min(10001, max(1, times))
        results = Counter(random.choice(choices) for _ in range(times))
        builder = []
        if len(results) > 10:
            builder.append("Only showing top 10 results...")
        for index, (elem, count) in enumerate(results.most_common(10), start=1):
            builder.append(f"{index}. {elem} ({plural(count):time}, {count/times:.2%})")

        await ctx.send("\n".join(builder))

    @commands.command()
    async def roll(self, ctx: Context, *dice: DiceType if TYPE_CHECKING else DiceRoll) -> None:
        """Roll DnD die!"""
        if len(dice) >= 25:
            await ctx.send("No more than 25 rolls per invoke, please.")
            return

        embed = discord.Embed(title="Rolls", colour=discord.Colour.random())

        for i in dice:
            fmt = ""
            total = i["totals"]
            die = i["die"]
            rolls = i["rolls"]

            builder = []
            roll_sum = 0
            for count, roll in enumerate(total, start=1):
                builder.append(f"{count}: {roll}")
                roll_sum += roll
            fmt += "\n".join(builder)
            fmt += f"\nSum: {roll_sum}\n"

            embed.add_field(name=f"{rolls}d{die}", value=to_codeblock(fmt, language="prolog"))

        embed.set_footer(text=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)

        await ctx.send(embed=embed)

    @roll.error
    async def roll_error(self, ctx: Context, error: BaseException) -> None:
        error = getattr(error, "original", error)

        if isinstance(error, commands.BadArgument):
            await ctx.send(str(error), delete_after=5)
            return


async def setup(bot: Kukiko) -> None:
    await bot.add_cog(RNG(bot))
