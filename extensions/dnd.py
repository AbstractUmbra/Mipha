from __future__ import annotations

import random
import re
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from utilities.context import Context
from utilities.formats import to_codeblock


if TYPE_CHECKING:
    from bot import Kukiko

DICE_RE = re.compile(r"(?P<rolls>[0-9]+)d(?P<die>[0-9]+)(?P<mod>[\+\-][0-9]+)?")


class Roll:
    def __init__(self, *, die: int, rolls: int, mod: str | None = None, mod_amount: int | None = None) -> None:
        self.die: int = die
        self.rolls: int = rolls
        self.mod: str | None = mod
        self.mod_amount: int | None = mod_amount

    def __str__(self) -> str:
        fmt = f"{self.rolls}d{self.die}"
        if self.mod:
            fmt += f"{self.mod}{self.mod_amount}"

        return fmt

    def __repr__(self) -> str:
        return f"<Roll die={self.die} rolls={self.rolls} mod={self.mod} mod_amount={self.mod_amount}>"


class DiceRoll(commands.Converter[Roll]):
    async def convert(self, _: Context, argument: str) -> list[Roll]:
        search: list[tuple[str, str, str]] = DICE_RE.findall(argument)
        if not search:
            raise commands.BadArgument("Dice roll doesn't seem valid, please use it in the format of `2d20` or `2d20+8`.")

        ret: list[Roll] = []

        for match in search:
            rolls: int = int(match[0])
            die: int = int(match[1])
            if potential_mod := match[2]:
                mod: str | None = potential_mod[0]
                mod_amount: int | None = int(potential_mod[1:])
            else:
                mod = None
                mod_amount = None

            ret.append(Roll(die=die, rolls=rolls, mod=mod, mod_amount=mod_amount))

        return ret


class DnD(commands.GroupCog, name="dnd"):
    def __init__(self, bot: Kukiko) -> None:
        self.bot: Kukiko = bot
        self._classes: list[str] | None = None
        super().__init__()

    @commands.hybrid_command()
    async def roll(
        self,
        ctx: Context,
        *,
        dice: list[Roll] = commands.param(converter=DiceRoll, default=None, displayed_default="1d20+0"),
    ) -> None:
        """
        Roll DnD die!

        Rolls a DnD die in the format of `1d10+0`, this includes `+` or `-` modifiers.
        Examples:
            `1d10+2`
            `2d8-12`

        You can also roll multiple dice at once, in the format of `2d10+2 1d12`.
        """
        dice = dice or [Roll(die=20, rolls=1)]
        if len(dice) >= 25:
            await ctx.send("No more than 25 rolls per invoke, please.")
            return

        embed = discord.Embed(title="Rolls", colour=discord.Colour.random())

        for idx, die in enumerate(dice, start=1):
            _choices: list[int] = []
            for _ in range(die.rolls):
                _choices.append(random.randint(1, die.die))
            _current_total: int = sum(_choices)

            fmt = ""

            for idx, amount in enumerate(_choices, start=1):
                fmt += f"Roll {idx}: {amount}\n"

            fmt += f"\nTotal: {_current_total}"
            if die.mod:
                assert die.mod_amount
                if die.mod == "+":
                    _current_total += die.mod_amount
                elif die.mod == "-":
                    _current_total -= die.mod_amount
                fmt += f"\nTotal incl mod: {abs(_current_total)}"

            embed.add_field(name=f"{die}", value=to_codeblock(fmt, language="prolog"))
            _current_total = 0

        embed.set_footer(text=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)

        await ctx.send(embed=embed)

    @roll.error
    async def roll_error(self, ctx: Context, error: BaseException) -> None:
        error = getattr(error, "original", error)

        if isinstance(error, commands.BadArgument):
            await ctx.send(str(error), delete_after=5)
            return


async def setup(bot: Kukiko) -> None:
    await bot.add_cog(DnD(bot))
