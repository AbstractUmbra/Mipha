from __future__ import annotations

import random as rng
from collections import Counter
from typing import TYPE_CHECKING

from discord.ext import commands
from typing_extensions import Annotated

from utilities.formats import plural


if TYPE_CHECKING:
    from bot import Mipha
    from utilities.context import Context


class RNG(commands.Cog):
    """Utilities that provide pseudo-RNG."""

    def __init__(self, bot: Mipha, /) -> None:
        self.bot: Mipha = bot

    @commands.group()
    async def random(self, ctx: Context):
        """Displays a random thing you request."""
        if ctx.invoked_subcommand is None:
            await ctx.send(f"Incorrect random subcommand passed. Try {ctx.prefix}help random")

    @random.command()
    async def number(self, ctx: Context, minimum: int = 0, maximum: int = 100):
        """Displays a random number within an optional range.

        The minimum must be smaller than the maximum and the maximum number
        accepted is 1000.
        """

        maximum = min(maximum, 1000)
        if minimum >= maximum:
            await ctx.send("Maximum is smaller than minimum.")
            return

        await ctx.send(str(rng.randint(minimum, maximum)))

    @random.command()
    async def lenny(self, ctx: Context):
        """Displays a random lenny face."""
        lenny = rng.choice(
            [
                "( ͡° ͜ʖ ͡°)",
                "( ͠° ͟ʖ ͡°)",
                "ᕦ( ͡° ͜ʖ ͡°)ᕤ",
                "( ͡~ ͜ʖ ͡°)",
                "( ͡o ͜ʖ ͡o)",
                "͡(° ͜ʖ ͡ -)",
                "( ͡͡ ° ͜ ʖ ͡ °)﻿",
                "(ง ͠° ͟ل͜ ͡°)ง",
                "ヽ༼ຈل͜ຈ༽ﾉ",
            ]
        )
        await ctx.send(lenny)

    @commands.command()
    async def choose(self, ctx: Context, *choices: Annotated[str, commands.clean_content]):
        """Chooses between multiple choices.

        To denote multiple choices, you should use double quotes.
        """
        if len(choices) < 2:
            return await ctx.send("Not enough choices to pick from.")

        await ctx.send(rng.choice(choices))

    def _bestof_choices(self, first: str, second: str, best_of: int) -> tuple[str, list[str]]:
        """Plays a best of N game between two choices and returns the status of each game in a list."""
        wins_needed = (best_of // 2) + 1
        wins: list[int] = [0, 0]
        results: list[str] = []
        for _ in range(best_of):
            winner = rng.choice([0, 1])
            wins[winner] += 1
            choice = first if winner == 0 else second
            results.append(choice)
            if wins[winner] == wins_needed:
                return (choice, results)

        raise RuntimeError("Unreachable")

    def _simulate_double_elimination(self, first: str, second: str, third: str) -> list[str]:
        """Simulates a double elimination tournament between three choices."""

        # Bracket visualisation:
        # T1 vs T2 => W1
        # T3 vs W1 => W2
        # L1 vs L2 => W3
        # W2 vs W3 => W4
        # if W2 wins => champion
        # if W3 wins, W2 vs W3 again for champion

        to_send: list[str] = []
        # First round is Bo3
        winner, results = self._bestof_choices(first, second, 3)
        formatted_results = ", ".join("Win" if r == winner else "Loss" for r in results)
        to_send.append(f"1. {first} vs {second}: {winner} wins! ({formatted_results})")
        loser = first if winner == second else second
        # Second round is also Bo3
        second_winner, results = self._bestof_choices(winner, third, 3)
        formatted_results = ", ".join("Win" if r == second_winner else "Loss" for r in results)
        to_send.append(f"2. {winner} vs {third}: {second_winner} wins! ({formatted_results})")

        # Third round is loser's bracket Bo3
        second_loser = winner if second_winner == third else third
        third_winner, results = self._bestof_choices(loser, second_loser, 3)
        formatted_results = ", ".join("Win" if r == third_winner else "Loss" for r in results)
        to_send.append(f"3. {loser} vs {second_loser}: {third_winner} wins! ({formatted_results})")
        eliminated = loser if third_winner == second_loser else second_loser
        to_send.append(f"  - {eliminated} is eliminated!")

        # Championship rounds are Bo5
        fourth_winner, results = self._bestof_choices(second_winner, third_winner, 5)
        formatted_results = ", ".join("Win" if r == fourth_winner else "Loss" for r in results)
        if fourth_winner == second_winner:
            to_send.append(
                f"4. {second_winner} vs {third_winner}: **{fourth_winner} won the championship ({formatted_results})!**"
            )
            return to_send
        else:
            to_send.append(f"4. {second_winner} vs {third_winner}: {fourth_winner} wins! ({formatted_results})")

        # Upset round
        champion, results = self._bestof_choices(second_winner, third_winner, 5)
        formatted_results = ", ".join("Win" if r == champion else "Loss" for r in results)
        to_send.append(f"5. **{champion} won the championship ({formatted_results})!**")
        return to_send

    def generate_round_robin(self, choices: list[str | None]) -> list[list[tuple[str | None, str | None]]]:
        """Generates a round robin tournament between the choices."""
        # Bye marker
        if len(choices) % 2:
            choices.append(None)

        half = len(choices) // 2
        schedule: list[list[tuple[str | None, str | None]]] = []
        for _ in range(len(choices) - 1):
            schedule.append([(choices[j], choices[-j - 1]) for j in range(half)])
            choices.insert(1, choices.pop())

        return schedule

    def simulate_round_robin(self, choices: list[str]) -> list[str]:
        """Simulates a round robin tournament between the choices."""
        schedule = self.generate_round_robin(choices)  # type: ignore
        to_send: list[str] = []
        winners = Counter()
        for index, round in enumerate(schedule, start=1):
            to_send.append(f"**Round {index}:**")
            for first, second in round:
                if first is None:
                    to_send.append(f"- {second} gets a bye!")
                    winners[second] += 1
                    continue

                if second is None:
                    to_send.append(f"- {first} gets a bye!")
                    winners[first] += 1
                    continue

                winner, results = self._bestof_choices(first, second, 3)
                formatted_results = ", ".join("Win" if r == winner else "Loss" for r in results)
                to_send.append(f"- {first} vs {second}: {winner} wins! ({formatted_results})")
                winners[winner] += 1

        to_send.append("**Final Results:**")
        for winner, wins in winners.most_common():
            to_send.append(f"- {winner} has {plural(wins):win}")

        return to_send

    @commands.command()
    async def choosebestof(self, ctx: Context, *choices: Annotated[str, commands.clean_content(escape_markdown=True)]):
        """Chooses between multiple choices in a tournament style."""
        if len(choices) < 2:
            return await ctx.send("Not enough choices to pick from.")

        if len(choices) > 10:
            return await ctx.send("Too many choices to pick from.")

        results: list[str] = []
        if len(choices) == 2:
            first, second = choices
            winner, games = self._bestof_choices(first, second, 5)
            results.append(f"{first} vs {second}:")
            for index, result in enumerate(games, start=1):
                results.append(f"Round {index}: {result} wins")
            results.append(f"**{winner} wins**")
        elif len(choices) == 3:
            results = self._simulate_double_elimination(choices[0], choices[1], choices[2])
        else:
            results = self.simulate_round_robin(list(choices))

        await ctx.send("\n".join(results))


async def setup(bot: Mipha) -> None:
    await bot.add_cog(RNG(bot))
