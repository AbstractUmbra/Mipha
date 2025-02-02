"""
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at http://mozilla.org/MPL/2.0/.
"""

from __future__ import annotations

import asyncio
import csv
import datetime
import logging
import random
import time
from collections import defaultdict
from functools import partial
from io import BytesIO
from textwrap import dedent, fill
from typing import TYPE_CHECKING, Literal
from urllib.parse import quote

import aiohttp
import bs4
import discord
import pykakasi
from discord.ext import commands, tasks
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from utilities.shared.converters import MemeDict
from utilities.shared.formats import plural, to_codeblock
from utilities.shared.nihongo import JishoWord, KanjiDevKanji, KanjiDevWords
from utilities.shared.paginator import RoboPages, SimpleListSource

if TYPE_CHECKING:
    from typing import Self

    from bot import Mipha
    from utilities.context import Context
    from utilities.shared._types.nihongo import (
        JishoWordsResponse,
        KanjiDevKanjiPayload,
        KanjiDevWordsPayload,
        _JishoJapanesePayload,
    )

BASE_URL = "https://kanjiapi.dev/v1"
HIRAGANA = (
    "あいうえおかきくけこがぎぐげごさしすせそざじずぜぞたちつてとだぢづでどなにぬ"
    "ねのはひふへほばびぶべぼぱぴぷぺぽまみむめもやゆよらりるれろわを"
)
KATAKANA = (
    "アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメ"
    "モヤユヨラリルレロワヲンガギグゲゴザジズゼゾダヂヅデドバビブベボパピプペポ"
)
JISHO_WORDS_URL = "https://jisho.org/api/v1/search/words"
JISHO_KANJI_URL = "https://jisho.org/api/v1/search/{}%23kanji"
JISHO_REPLACEMENTS = {
    "english_definitions": "Definitions",
    "parts_of_speech": "Type",
    "tags": "Notes",
    "see_also": "See Also",
}
JLPT_N1 = list(csv.reader(open("static/jlpt/n1.csv", encoding="utf-8")))  # noqa: SIM115, PTH123
JLPT_N2 = list(csv.reader(open("static/jlpt/n2.csv", encoding="utf-8")))  # noqa: SIM115, PTH123
JLPT_N3 = list(csv.reader(open("static/jlpt/n3.csv", encoding="utf-8")))  # noqa: SIM115, PTH123
JLPT_N4 = list(csv.reader(open("static/jlpt/n4.csv", encoding="utf-8")))  # noqa: SIM115, PTH123
JLPT_N5 = list(csv.reader(open("static/jlpt/n5.csv", encoding="utf-8")))  # noqa: SIM115, PTH123
JLPT_LOOKUP = MemeDict(
    {
        ("n1", "ｎ１", "1", "１"): JLPT_N1,
        ("n2", "ｎ２", "2", "２"): JLPT_N2,
        ("n3", "ｎ３", "3", "３"): JLPT_N3,
        ("n4", "ｎ４", "4", "４"): JLPT_N4,
        ("n5", "ｎ５", "5", "５"): JLPT_N5,
    },
)

LOGGER = logging.getLogger(__name__)


class JLPTConverter(commands.Converter[list[str]]):
    async def convert(self, _: Context, argument: str) -> list[str]:
        try:
            return JLPT_LOOKUP[argument.lower().strip()]
        except KeyError as err:
            raise commands.BadArgument("Invalid key for JLPT level.") from err


def word_to_reading(stuff: list[_JishoJapanesePayload]) -> list[str]:
    ret = []
    for item in stuff:
        if item.get("word"):
            hmm = f"{item['word']} 【{item['reading']}】" if item.get("reading") else f"{item['word']}"
            ret.append(hmm)
    return ret


def kanji_in_response(kanji: str, soup: bs4.BeautifulSoup) -> bool:
    segment = f'<h1 class="character" data-area-name="print" lang="ja">{kanji}</h1>'
    raw = soup.find("h1", class_="character")
    if raw is None:
        return False
    return segment in raw


def parse_response(raw_html: str) -> bs4.BeautifulSoup:
    return bs4.BeautifulSoup(raw_html, "html.parser")


class JishoKanji:
    def __init__(self, kanji: str, data: bs4.BeautifulSoup, url: str) -> None:
        self.kanji = kanji
        self.data = data
        self.url = url

    @property
    def taught_in(self) -> str | None:
        raw = self.data.find("div", class_="grade")
        if raw:
            return raw.select("strong")[0].text.title()  # pyright: ignore[reportAttributeAccessIssue] # bs4 types are bad
        return None

    @property
    def jlpt_level(self) -> str | None:
        raw = self.data.find("div", class_="jlpt")
        if raw is None:
            return None

        level = raw.select("strong")[0].text  # pyright: ignore[reportAttributeAccessIssue] # bs4 types are bad
        return level.title()

    @property
    def stroke_count(self) -> str | None:
        raw = self.data.find("div", class_="kanji-details__stroke_count")
        if raw is None:
            return None

        count = raw.select("strong")[0].text  # pyright: ignore[reportAttributeAccessIssue] # bs4 types are bad

        return f"{plural(int(count)):Stroke}"

    @property
    def stroke_url(self) -> str:
        return f"https://raw.githubusercontent.com/mistval/kanji_images/master/gifs/{ord(self.kanji):x}.gif?v=1"

    @property
    def meanings(self) -> str:
        raw = self.data.find("div", class_="kanji-details__main_meanings")
        if raw is None:
            raise ValueError("Something is None that shouldn't be None.")

        return raw.text.strip()

    @property
    def newspaper_frequency(self) -> str | None:
        raw = self.data.find("div", class_="frequency")
        if raw is None:
            return None

        raw = raw.select("strong")[0].text  # pyright: ignore[reportAttributeAccessIssue] # bs4 types are bad
        return f"{raw} of 2500 most used Kanji in newspapers."

    def reading_compounds(self) -> defaultdict[str, list[str]]:
        raw = self.data.find("div", class_="row compounds")
        if raw is None:
            raise ValueError("Something is None that shouldn't be None")

        fmt = defaultdict(list)

        for x in raw:  # pyright: ignore[reportAssignmentType] # typeshed sucks here
            x: bs4.Tag
            if isinstance(x, (bs4.NavigableString, str)):
                continue

            if hmm := x.select("h2"):
                if hmm[0].text == "On reading compounds":
                    fmt["On"] = [item.text.strip() for item in x.select("ul")]
                if hmm[0].text == "Kun reading compounds":
                    fmt["Kun"] = [item.text.strip() for item in x.select("ul")]

        return fmt

    def symbols(self, key: Literal["on", "kun"]) -> list[tuple[str, str]] | None:
        raw = self.data.find("div", class_="kanji-details__main-readings")
        if raw is None:
            return None

        raw = raw.find("dl", class_=f"dictionary_entry {key}_yomi")  # pyright: ignore[reportCallIssue] # bs4 types are bad
        if not raw:
            return None

        raw = raw.select("dd", class_="kanji-details__main-readings")[0]  # pyright: ignore[reportAttributeAccessIssue,reportCallIssue] # bs4 types are bad

        fmt = []

        for item in raw:
            if isinstance(item, bs4.element.Tag):
                text = item.text
                href = item.get("href")
                if href is None:
                    raise ValueError("Somethign was None that should not be None.")
                link = f"https://{href.lstrip('//')}"  # pyright: ignore[reportAttributeAccessIssue] # bs4 types are bad  # noqa: B005 # correct usage
                fmt.append((text, link))

        return fmt

    @property
    def on_readings(self) -> list[str] | None:
        readings = self.reading_compounds()
        if not readings:
            return None

        return readings["On"]

    @property
    def on_symbols(self) -> list[tuple[str, str]] | None:
        return self.symbols("on")

    @property
    def kun_readings(self) -> list[str] | None:
        readings = self.reading_compounds()
        if not readings:
            return None

        if kun_readings := readings.get("Kun"):
            return kun_readings

        return None

    @property
    def kun_symbols(self) -> list[tuple[str, str]] | None:
        return self.symbols("kun")

    @property
    def radical(self) -> list[str] | None:
        raw = self.data.find("div", class_="radicals")
        if raw and raw.find("span"):
            return raw.find("span").text.strip().rsplit()[:2]  # pyright: ignore[reportOptionalMemberAccess,reportAttributeAccessIssue] # guarded in earlier call
        return None

    def to_dict(self) -> dict[str, str | list[str]]:
        """Quick method to dump the object to a dict for JSON storage."""

        data = {}

        data["kanji"] = self.kanji
        data["url"] = self.url

        if self.taught_in:
            data["taught_in"] = self.taught_in

        if self.jlpt_level:
            data["jlpt_level"] = self.jlpt_level

        if self.stroke_count:
            data["stroke_count"] = self.stroke_count

        data["stroke_url"] = self.stroke_url

        data["meanings"] = self.meanings

        if self.newspaper_frequency:
            data["newspaper_frequency"] = self.newspaper_frequency

        readings = self.reading_compounds()
        on_readings = readings.get("On")
        if on_readings:
            data["on_readings"] = on_readings
        kun_readings = readings.get("Kun")
        if kun_readings:
            data["kun_readings"] = kun_readings

        on_symbols = self.symbols("on")
        if on_symbols:
            data["on_symbols"] = on_symbols
        kun_symbols = self.symbols("kun")
        if kun_symbols:
            data["kun_symbols"] = kun_symbols

        if self.radical:
            data["radical"] = self.radical

        return data


class KanjiEmbed(discord.Embed):
    @classmethod
    def from_kanji(cls: type[Self], payload: KanjiDevKanji) -> Self:
        embed = cls(title=payload.kanji, colour=discord.Colour(0xBF51B2))

        embed.add_field(name="(School) Grade learned:", value=f"**__{payload.grade}__**")
        embed.add_field(name="Stroke count:", value=f"**__{payload.stroke_count}__**")
        embed.add_field(name="Kun Readings", value=(payload.kun_readings or "N/A"))
        embed.add_field(name="On Readings", value=(payload.on_readings or "N/A"))
        embed.add_field(name="Name Readings", value=(payload.name_readings or "N/A"))
        embed.add_field(name="Unicode", value=payload.unicode)
        embed.description = to_codeblock(payload.meanings or "N/A", language="")
        embed.set_footer(text=f"JLPT Grade: {payload.jlpt_level or 'N/A'}")

        return embed

    @classmethod
    def from_words(cls: type[Self], character: str, payload: KanjiDevWords) -> list[KanjiEmbed]:
        embeds: list[KanjiEmbed] = []
        variants = payload.variants
        meanings = payload.meanings()
        for variant in variants:
            embed = cls(title=character, colour=discord.Colour(0x4AFAFC))

            embed.add_field(name="Written:", value=variant["written"])
            embed.add_field(name="Pronounced:", value=variant["pronounced"])
            priorities = to_codeblock("".join(variant["priorities"]), language="") if variant["priorities"] else "N/A"
            embed.add_field(name="Priorities:", value=priorities)
            for _ in range(3):
                embed.add_field(name="\u200b", value="\u200b")
            embed.add_field(name="Kanji meaning(s):", value=meanings)

            embeds.append(embed)

        return embeds

    @classmethod
    def from_jisho(cls: type[Self], query: str, payload: JishoWord) -> Self:
        embed = cls(title=f"Jisho data on {query}.", colour=discord.Colour(0x4AFAFC))

        attributions = []
        for key, value in payload.attributions.items():
            if value is True:
                attributions.append(key.title())
            elif value is False:
                continue
            elif value:
                attributions.append(f"{key.title()}: {value}")

        if attributions:
            attributions_cb = to_codeblock("\n".join(attributions), language="prolog", escape_md=False)
            embed.add_field(name="Attributions", value=attributions_cb, inline=False)

        jp = word_to_reading(payload.words_and_readings)

        japanese = "\n\n".join(jp)
        embed.add_field(
            name="Writing 【Reading】",
            value=to_codeblock(japanese, language="prolog", escape_md=False),
            inline=False,
        )

        sense = payload.senses[0]
        senses = ""
        links = ""
        sources = ""
        embed.description = ""
        for key, value in sense.items():
            if key == "links":
                if value:
                    subdict = value[0]  # pyright: ignore[reportIndexIssue]  # typeddict.items funny
                    links += f"[{subdict.get('text')}]({subdict.get('url')})\n"
                else:
                    continue
            elif key == "source":
                if value:
                    subdict = value[0]  # pyright: ignore[reportIndexIssue]  # typeddict.items funny
                    sources += f"Language: {subdict['language']}\nWord: {subdict['word']}"
            else:
                if value:
                    senses += f"{JISHO_REPLACEMENTS.get(key, key).title()}: {', '.join(value)}\n"  # pyright: ignore[reportArgumentType,reportCallIssue]  # bleh webscrape code

        if senses:
            embed.description += to_codeblock(senses, language="prolog", escape_md=False)

        if links:
            embed.description += links

        if sources:
            embed.description += "\nSources:"
            embed.description += to_codeblock(sources, language="prolog", escape_md=False)

        embed.add_field(
            name="Is it common?",
            value=("Yes" if payload.is_common else "No"),
            inline=False,
        )

        if payload.jlpt:
            embed.add_field(name="JLPT Level", value=payload.jlpt[0], inline=False)

        embed.set_footer(text=f"Slug: {payload.slug}")

        return embed


class Nihongo(commands.Cog):
    """The description for Nihongo goes here."""

    def __init__(self, bot: Mipha) -> None:
        self.bot = bot
        self.kakasi = pykakasi.kakasi()

    @commands.command()
    async def romaji(self, ctx: Context, *, text: str = commands.param(converter=commands.clean_content)) -> None:
        """Sends the Romaji version of passed Kana."""
        ret = await self.bot.loop.run_in_executor(None, self.kakasi.convert, text)
        conjoined = " ".join(piece["hepburn"] for piece in ret)
        await ctx.send(conjoined)

    @commands.group(name="kanji", aliases=["かんじ", "漢字"], invoke_without_command=True)
    async def kanji(self, ctx: Context, character: str) -> None:
        """KanjiApi.dev - Return data on a single Kanji."""
        if len(character) > 1:
            raise commands.BadArgument("Only one Kanji please.")
        url = f"{BASE_URL}/kanji/{character}"

        async with self.bot.session.get(url) as response:
            data: KanjiDevKanjiPayload = await response.json()

        kanji_data = KanjiDevKanji(data)

        embed = KanjiEmbed.from_kanji(kanji_data)

        menu = RoboPages(SimpleListSource([embed]), ctx=ctx)
        await menu.start()

    @kanji.command(name="words")
    async def words(self, ctx: Context, character: str) -> None:
        """KanjiApi.dev - Return the words a Kanji is used in, or in conjuction with."""
        if len(character) > 1:
            raise commands.BadArgument("Only one Kanji please.")
        url = f"{BASE_URL}/words/{character}"

        async with self.bot.session.get(url) as response:
            data: list[KanjiDevWordsPayload] = await response.json()

        words_data = [KanjiDevWords(payload) for payload in data]
        embeds = [KanjiEmbed.from_words(character, kanji) for kanji in words_data]
        real_embeds = [embed for sublist in embeds for embed in sublist]

        fixed_embeds = [
            embed.set_footer(
                text=(
                    f"{embed.footer.text} :: {real_embeds.index(embed) + 1}/{len(real_embeds)}"
                    if embed.footer.text
                    else f"{real_embeds.index(embed) + 1}/{len(real_embeds)}"
                ),
            )
            for embed in real_embeds
        ]

        menu = RoboPages(SimpleListSource(fixed_embeds), ctx=ctx)
        await menu.start()

    @kanji.error
    @words.error
    async def nihongo_error(self, ctx: Context, error: commands.CommandError) -> None:
        error = getattr(error, "original", error)

        if isinstance(error, aiohttp.ContentTypeError):
            await ctx.send("You appear to have passed an invalid *kanji*.")
            return

    @commands.command()
    async def jisho(self, ctx: Context, *, query: str) -> None:
        """Query the Jisho api with your kanji/word."""
        async with self.bot.session.get(JISHO_WORDS_URL, params={"keyword": query}) as response:
            if response.status == 200:
                data: JishoWordsResponse = await response.json()
            else:
                raise commands.BadArgument("Not a valid query for Jisho.")

            if not data["data"]:
                raise commands.BadArgument("Not a valid query for Jisho.")

        jisho_data = [JishoWord(payload) for payload in data["data"]]
        embeds = [KanjiEmbed.from_jisho(query, item) for item in jisho_data]

        fixed_embeds = [
            embed.set_footer(
                text=(
                    f"{embed.footer.text} :: {embeds.index(embed) + 1}/{len(embeds)}"
                    if embed.footer.text
                    else f"{embeds.index(embed) + 1}/{len(embeds)}"
                ),
            )
            for embed in embeds
        ]

        menu = RoboPages(SimpleListSource(fixed_embeds), ctx=ctx)
        await menu.start()

    def _draw_kana(self, text: str) -> BytesIO:
        text = fill(text, 25, replace_whitespace=False)
        font = ImageFont.truetype("static/fonts/NotoSans-Bold.ttf", 60)
        padding = 50

        images = [Image.new("RGBA", (1, 1), color=0) for _ in range(2)]
        for index, (image, colour) in enumerate(zip(images, ((47, 49, 54), "white"), strict=False)):
            draw = ImageDraw.Draw(image)
            left, top, right, bottom = draw.multiline_textbbox((0, 0), text, font=font)
            w, h = right - left, bottom - top
            images[index] = image = image.resize((w + padding, h + padding))  # noqa: PLW2901 # correct usage
            draw = ImageDraw.Draw(image)
            draw.multiline_text((padding / 2, padding / 2), text=text, fill=colour, font=font)
        background, foreground = images

        background = background.filter(ImageFilter.GaussianBlur(radius=7))
        background.paste(foreground, (0, 0), foreground)
        buf = BytesIO()
        background.save(buf, "png")
        buf.seek(0)
        return buf

    @commands.command()
    async def kana(self, ctx: Context, *, text: str) -> None:
        """
        Returns an image representing the passed text.
        """
        func = partial(self._draw_kana, text)
        img = await ctx.bot.loop.run_in_executor(None, func)

        file = discord.File(fp=img, filename="kana.png")

        await ctx.send(file=file)

    @commands.command()
    @commands.cooldown(1, 10, commands.BucketType.channel)
    async def kanarace(self, ctx: Context, amount: int = 10, kana: Literal["k", "h"] | None = "h") -> None:
        """Kana racing.

        This command will send an image of a string of Kana of [amount] length.
        Please type and send this Kana in the same channel to qualify.
        """

        if kana not in ("k", "h"):
            kana = "k"

        chars = HIRAGANA if kana == "h" else KATAKANA

        amount = max(min(amount, 50), 5)

        await ctx.send("Kana-racing begins in 5 seconds.")
        await asyncio.sleep(5)

        randomized_kana = "".join(random.choices(chars, k=amount))  # noqa: S311 # not crypto

        func = partial(self._draw_kana, randomized_kana)
        image = await ctx.bot.loop.run_in_executor(None, func)
        file = discord.File(fp=image, filename="kanarace.png")
        await ctx.send(file=file)

        winners = {}
        is_ended = asyncio.Event()

        start = time.time()

        def check(message: discord.Message) -> bool:
            if (
                message.channel == ctx.channel
                and message.content.lower() == randomized_kana
                and message.author not in winners
            ):
                winners[message.author] = time.time() - start
                is_ended.set()
                ctx.bot.loop.create_task(message.add_reaction(ctx.tick(True)))  # noqa: FBT003 # shortcut
            return False

        task = ctx.bot.loop.create_task(ctx.bot.wait_for("message", check=check))

        try:
            await asyncio.wait_for(is_ended.wait(), timeout=60)
        except TimeoutError:
            await ctx.send("No participants matched the output.")
        else:
            await ctx.send("Word accepted... Other players have 10 seconds left.")
            await asyncio.sleep(10)
            embed = discord.Embed(title=f"{plural(len(winners)):Winner}", colour=discord.Colour.random())
            embed.description = "\n".join(
                f"{idx}: {person.mention} - {time:.4f} seconds for {amount / time * 60:.2f} kana per minute"
                for idx, (person, time) in enumerate(winners.items(), start=1)
            )

            await ctx.send(embed=embed)
        finally:
            task.cancel()

    @kanarace.error
    async def race_error(self, ctx: Context, error: commands.CommandError) -> None:
        if isinstance(error, asyncio.TimeoutError):
            await ctx.send("Kanarace has no winners!", delete_after=5.0)
            return

    @commands.command()
    async def jlpt(
        self,
        ctx: Context,
        level: list[str] = commands.param(converter=JLPTConverter, default=JLPT_N5, displayed_default="n5"),  # noqa: B008 # this is how commands.param works
    ) -> None:
        """
        Returns a random word from the specified JLPT level.
        """
        word, reading, meaning, _ = random.choice(level)  # noqa: S311 # not crypto
        embed = discord.Embed(title=word, description=meaning, colour=discord.Colour.random())
        embed.add_field(name="Reading", value=f"『{reading}』")

        await ctx.send(embed=embed)

    def _gen_kanji_embed(self, payloads: list[JishoKanji]) -> list[discord.Embed]:
        returns = []
        for data in payloads:
            stroke = discord.Embed(title=data.kanji, url=data.url)
            stroke.set_image(url=data.stroke_url)
            strokes = data.stroke_count or "Not a Kanji"
            stroke.add_field(name="Stroke Count", value=strokes)
            stroke.add_field(name="JLPT Level", value=data.jlpt_level)
            if data.radical:
                stroke.add_field(name="Radical", value=f"({data.radical[1]}) {data.radical[0]}")
            returns.append(stroke)

            if data.on_symbols:
                on_embed = discord.Embed(title=data.kanji, url=data.url)
                on_sym = "\n".join(f"[{item[0]}]({item[1]})" for item in data.on_symbols)
                on_embed.add_field(name="On symbols", value=on_sym)
                if data.on_readings:
                    on = "\n".join(data.on_readings)
                    on_embed.add_field(
                        name="On readings",
                        value=to_codeblock(f"{dedent(on)}", language="", escape_md=False),
                        inline=False,
                    )
                returns.append(on_embed)
            if data.kun_symbols:
                kun_embed = discord.Embed(title=data.kanji, url=data.url)
                kun_sym = "\n".join(f"[{item[0]}]({item[1]})" for item in data.kun_symbols)
                kun_embed.add_field(name="Kun symbols", value=kun_sym)
                if data.kun_readings:
                    kun = "\n".join(data.kun_readings)
                    kun_embed.add_field(
                        name="Kun readings",
                        value=to_codeblock(f"{dedent(kun)}", language="", escape_md=False),
                        inline=False,
                    )
                returns.append(kun_embed)

        return returns

    @commands.command(name="strokeorder", aliases=["so"])
    async def stroke_order(self, ctx: Context, *, kanji: str) -> None:
        """
        Returns an animation of the stroke order of the provided kana/kanji.
        """
        if len(kanji) > 5:
            await ctx.send("fuck off ava, clamped at 5")
            kanji = kanji[:5]

        responses = []
        for char in kanji:
            url = quote(f"https://jisho.org/search/{char}#kanji", safe="/:?&")
            data = await ctx.bot.session.get(url)
            soup = bs4.BeautifulSoup(await data.content.read(), "html.parser")
            response = JishoKanji(char, soup, url)
            responses.append(response)

        embeds = self._gen_kanji_embed(responses)
        source = SimpleListSource(embeds)
        menu = RoboPages(source=source, ctx=ctx)
        await menu.start()

    @tasks.loop(hours=24)
    async def nihongo_study_reminders(self) -> None:
        message = "Hey <@155863164544614402>, you need to study Japanese now."
        dm_channel = self.bot.owner.dm_channel or await self.bot.owner.create_dm()
        await dm_channel.send(message, allowed_mentions=discord.AllowedMentions(users=True))

    @nihongo_study_reminders.before_loop
    async def before_nihongo_reminder(self) -> None:
        await self.bot.wait_until_ready()

        now = datetime.datetime.now(datetime.UTC)
        time = datetime.time(hour=18, minute=0, second=0, microsecond=0)
        if now.time() > time:
            LOGGER.info("After 6pm, sleeping until tomorrow.")
            sleep_until = now + datetime.timedelta(days=1)
            sleep_until = sleep_until.replace(hour=18, minute=0, second=0, microsecond=0)
        else:
            sleep_until = now.replace(hour=18, minute=0, second=0, microsecond=0)

        await discord.utils.sleep_until(sleep_until)


async def setup(bot: Mipha) -> None:
    await bot.add_cog(Nihongo(bot))
