"""
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at http://mozilla.org/MPL/2.0/.
"""

from __future__ import annotations

import asyncio
import pathlib
import random
import re
from collections import defaultdict
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from utilities.context import Context


if TYPE_CHECKING:
    from bot import Kukiko

EMOJI: dict[bool | None, str] = {
    True: "\U0001f7e9",
    False: "\U0001f7e5",
    None: "\U0001f7e7",
}

WORDS_PATH = pathlib.Path("utilities/scrabble.txt")
with WORDS_PATH.open("r") as fp:
    WORDS = fp.readlines()

CLEAN_WORDS: defaultdict[int, list[str]] = defaultdict(list)

for word in WORDS:
    CLEAN_WORDS[len(word.strip("\n"))].append(word.lower().strip("\n"))


class GuessesExceeded(Exception):
    def __init__(self, message: str) -> None:
        self.message: str = message
        super().__init__(message)

    def __str__(self) -> str:
        return self.message


# def check_guesses(func: Callable[Concatenate[C, P], T]) -> Callable[Concatenate[C, P], T]:
#     """A decorator to assure the `self` parameter of decorated methods has authentication set."""

#     @wraps(func)
#     def wrapper(item: C, *args: P.args, **kwargs: P.kwargs) -> T:
#         if item._guesses >= 5:
#             item.over = True
#             item.solved = False
#             raise GuessesExceeded("You guessed 5 times and did not get the correct answer!")

#         return func(item, *args, **kwargs)

#     return wrapper


class WordleGame:
    __slots__ = (
        "_answer",
        "_guesses",
        "_last_guess",
        "_message",
        "_owner",
        "_current_map",
        "over",
        "solved",
    )

    def __init__(self, *, answer: str, owner: int) -> None:
        self._answer: str = answer
        self._guesses: int = 0
        self._last_guess: str = ""
        self._message: discord.Message | None = None
        self._owner: int = owner
        self._current_map: str = f"<@{self._owner}>\n"
        self.over: bool = False
        self.solved: bool = False

    # @check_guesses
    def guess(self, *, guess: str) -> str:
        ret: str = ""
        for guess_char, answer_char in zip(guess, self._answer):
            ret += EMOJI[guess_char == answer_char]
            ret += "\N{ZERO WIDTH SPACE}"
        self._current_map += f"\n{ret}"

        self._guesses += 1
        self._last_guess = ret

        if self._guesses >= 5 and not self.solved:
            raise GuessesExceeded(
                f"You guessed 5 times and did not get the correct answer, which was: || {self._answer} ||"
                f" Final output:{self._current_map}"
            )

        if guess == self._answer:
            self.over = True
            self.solved = True

        return self._current_map


def unspoiler(match: re.Match[str]) -> str:
    return match.group(0)


class DLECog(commands.Cog):
    def __init__(self, bot: Kukiko) -> None:
        self.bot: Kukiko = bot

    def get_wordle_word(self, *, size: int) -> str:
        return random.choice(CLEAN_WORDS[size])

    @commands.command(name="wordle")
    @commands.max_concurrency(number=1, per=commands.BucketType.user, wait=False)
    async def wordle_command(self, ctx: Context, word_length: int = 6) -> None:
        """Launches a game of wordle, with a hidden word of the given lenth.

        Sending `stop`, `quit` or `cancel` will end the game early.
        These have been removed from the dictionary of words.
        """
        answer = self.get_wordle_word(size=word_length)
        game = WordleGame(answer=answer, owner=ctx.author.id)
        await ctx.send(f"Okay, start guessing the word. This game is locked to {ctx.author.mention}!")

        def wordle_check(message: discord.Message) -> bool:
            if message.author.id == ctx.author.id and message.channel.id == ctx.channel.id:
                if message.content in {"quit", "stop", "cancel"}:
                    game.over = True
                    return True
                if len(message.content) == len(game._answer):
                    return True

            return False

        while not game.over:
            try:
                message = await ctx.bot.wait_for(
                    "message",
                    check=wordle_check,
                    timeout=180,
                )
            except asyncio.TimeoutError:
                game.over = True
                if game._message:
                    await game._message.edit(
                        content=f"Sorry, you ran out of time to guess for this game. The correct word was || {game._answer} ||. Final output:\n{game._current_map}"
                    )
                else:
                    await ctx.send(
                        f"Sorry, you ran out of time to guess for this game. The correct word was || {game._answer} ||."
                    )
                return
            else:
                try:
                    current_guess = game.guess(guess=message.content.lower())
                except GuessesExceeded as error:
                    game.solved = False
                    game.over = True
                    if game._message:
                        await game._message.edit(content=f"{error}")
                else:
                    if game.solved:
                        current_guess += "\n\nCongratulations!!"

                    if game._message:
                        await game._message.edit(content=current_guess)
                    else:
                        game._message = await ctx.send(current_guess)


async def setup(bot: Kukiko) -> None:
    await bot.add_cog(DLECog(bot))
