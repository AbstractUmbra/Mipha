"""
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at http://mozilla.org/MPL/2.0/.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import io
import math
import pathlib
import random
import re
import time
from functools import partial
from string import ascii_lowercase
from textwrap import fill
from typing import TYPE_CHECKING

import aiohttp
import discord
import legofy
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from utilities import checks
from utilities.context import Context, GuildContext
from utilities.formats import plural


path_ = inspect.getabsfile(legofy.main)
resolved_path = pathlib.Path(path_).parent / "assets" / "bricks" / "1x1.png"

if TYPE_CHECKING:
    from bot import Mipha

ABT_REG = re.compile(r"~([a-zA-Z]+)~")
MESSAGE_LINK_RE = re.compile(
    r"^(?:https?://)(?:(?:canary|ptb)\.)?discord(?:app)?\.com/channels/(?P<guild>\d{16,20})/(?P<channel>\d{16,20})/(?P<message>\d{16,20})/?$"
)

MENTION_CHANNEL_ID = 722930330897743894
DM_CHANNEL_ID = 722930296756109322
SPOILER_EMOJI_ID = 738038828928860269

AL_BHED_CHARACTER_MAP = {
    "a": "y",
    "b": "p",
    "c": "l",
    "d": "t",
    "e": "a",
    "f": "v",
    "g": "k",
    "h": "r",
    "i": "e",
    "j": "z",
    "k": "g",
    "l": "m",
    "m": "s",
    "n": "h",
    "o": "u",
    "p": "b",
    "q": "x",
    "r": "n",
    "s": "c",
    "t": "d",
    "u": "i",
    "v": "j",
    "w": "f",
    "x": "q",
    "y": "o",
    "z": "w",
}


class Fun(commands.Cog):
    """Some fun stuff, not fleshed out yet."""

    def __init__(self, bot: Mipha) -> None:
        self.bot: Mipha = bot

    # @commands.Cog.listener("on_message")
    async def quote(self, message: discord.Message) -> None:
        if message.author.bot or message.embeds or message.guild is None:
            return

        if not message.guild or not message.guild.id == 149998214810959872:
            return

        assert isinstance(message.channel, discord.TextChannel)
        perms = message.channel.permissions_for(message.guild.me)
        if perms.send_messages is False or perms.embed_links is False:
            return

        if not (
            match := re.search(
                MESSAGE_LINK_RE,
                message.content,
            )
        ):
            return

        data = match.groupdict()
        guild_id = int(data["guild"])
        channel_id = int(data["channel"])
        message_id = int(data["message"])

        if guild_id != message.guild.id:
            return

        channel = message.guild.get_channel(channel_id)
        if channel is None:
            # deleted or private?
            return

        if channel.permissions_for(message.guild.default_role).read_messages is False:
            return

        assert isinstance(channel, discord.TextChannel)
        try:
            quote_message = await channel.fetch_message(message_id)
        except discord.HTTPException:
            # Bot has no access I guess.
            return

        embed = discord.Embed(title=f"Quote from {quote_message.author} in {channel.name}")
        embed.set_author(name=quote_message.author.name, icon_url=quote_message.author.display_avatar.url)
        embed.description = quote_message.content or "No message content."
        fmt = "This message had:\n"
        if quote_message.embeds:
            fmt += "one or more Embeds\n"
        if quote_message.attachments:
            fmt += "one or more Attachments\n"

        if len(fmt.split("\n")) >= 3:
            embed.add_field(name="Also...", value=fmt)

        embed.timestamp = quote_message.created_at

        await message.channel.send(embed=embed)

    @commands.group(invoke_without_command=True, skip_extra=False)
    async def abt(self, ctx: Context, *, content: str = commands.param(converter=commands.clean_content)) -> None:
        """I love this language."""
        keep = ABT_REG.findall(content)

        def trans(m: re.Match[str]) -> str:
            get = m.group(0)
            if get.isupper():
                return AL_BHED_CHARACTER_MAP[get.lower()].upper()
            return AL_BHED_CHARACTER_MAP[get]

        repl = re.sub("[a-zA-Z]", trans, content)
        fin = re.sub(ABT_REG, lambda _: keep.pop(0), repl)
        await ctx.send(fin)

    @abt.command(name="r", aliases=["reverse"])
    async def abt_reverse(self, ctx: Context, *, tr_input: str) -> None:
        """Uno reverse."""
        new_str = ""
        br = True
        for char in tr_input:
            if char == "~":
                br = not br
            if br and (char.lower() in ascii_lowercase):
                new_str += [key for key, val in AL_BHED_CHARACTER_MAP.items() if val == char.lower()][0]
            else:
                new_str += char
        await ctx.send(new_str.replace("~", "").capitalize())

    @commands.command()
    async def translate(
        self,
        ctx: Context,
        *,
        message: str | None = commands.param(converter=commands.clean_content, default=None),
    ) -> None:
        """Translates a message to English using DeepL's translation API."""

        if message is None:
            ref = ctx.message.reference
            if ref and isinstance(ref.resolved, discord.Message):
                new_content = ref.resolved.content
            else:
                await ctx.send("Missing a message to translate.")
                return
        else:
            new_content = message

        url = "https://api-free.deepl.com/v2/translate"
        form = aiohttp.FormData()
        form.add_field("auth_key", value=self.bot.config.DEEPL_KEY)
        form.add_field("text", value=new_content)
        form.add_field("target_lang", value="EN")

        response = await self.bot.session.post(url, data=form)
        data = await response.json()

        lang = data["translations"][0]["detected_source_language"]
        text = data["translations"][0]["text"]

        embed = discord.Embed(title="Translation:", colour=discord.Colour.random())
        embed.add_field(name="Source:", value=new_content, inline=False)
        embed.add_field(name=f"Translated from {lang}", value=text, inline=False)

        await ctx.send(embed=embed)

    def _draw_words(self, text: str) -> io.BytesIO:
        """."""
        text = fill(text, 25)
        font = ImageFont.truetype("static/W6.ttc", 60)
        padding = 50

        images = [Image.new("RGBA", (1, 1), color=0) for _ in range(2)]
        for index, (image, colour) in enumerate(zip(images, ((47, 49, 54), "white"))):
            draw = ImageDraw.Draw(image)
            w, h = draw.multiline_textsize(text, font=font)
            images[index] = image = image.resize((w + padding, h + padding))
            draw = ImageDraw.Draw(image)
            draw.multiline_text((padding / 2, padding / 2), text=text, fill=colour, font=font)
        background, foreground = images

        background = background.filter(ImageFilter.GaussianBlur(radius=7))
        background.paste(foreground, (0, 0), foreground)
        buf = io.BytesIO()
        background.save(buf, "png")
        buf.seek(0)
        return buf

    def random_words(self, amount: int) -> list[str]:
        with open("static/words.txt", "r") as fp:
            words = fp.readlines()

        return random.sample(words, amount)

    @commands.command(aliases=["typerace"])
    @commands.cooldown(1, 10, commands.BucketType.channel)
    @commands.max_concurrency(1, commands.BucketType.channel, wait=False)
    async def typeracer(self, ctx: Context, amount: int = 5) -> None:
        """
        Type racing.

        This command will send an image of words of [amount] length.
        Please type and send this Kana in the same channel to qualify.
        """

        amount = max(min(amount, 50), 1)

        await ctx.send("Type-racing begins in 5 seconds.")
        await asyncio.sleep(5)

        words = self.random_words(amount)
        randomized_words = (" ".join(words)).replace("\n", "").strip().lower()

        func = partial(self._draw_words, randomized_words)
        image = await ctx.bot.loop.run_in_executor(None, func)
        file = discord.File(fp=image, filename="typerace.png")
        await ctx.send(file=file)

        winners = dict()
        is_ended = asyncio.Event()

        start = time.time()

        def check(message: discord.Message) -> bool:
            if (
                message.channel == ctx.channel
                and not message.author.bot
                and message.content.lower() == randomized_words
                and message.author not in winners
            ):
                winners[message.author] = time.time() - start
                is_ended.set()
                ctx.bot.loop.create_task(message.add_reaction(ctx.tick(True)))
            return False

        task = ctx.bot.loop.create_task(ctx.bot.wait_for("message", check=check))

        try:
            await asyncio.wait_for(is_ended.wait(), timeout=60)
        except asyncio.TimeoutError:
            await ctx.send("No participants matched the output.")
        else:
            await ctx.send("Input accepted... Other players have 10 seconds left.")
            await asyncio.sleep(10)
            embed = discord.Embed(title=f"{plural(len(winners)):Winner}", colour=discord.Colour.random())
            embed.description = "\n".join(
                f"{idx}: {person.mention} - {time:.4f} seconds for {len(randomized_words) / time * 12:.2f}WPM"
                for idx, (person, time) in enumerate(winners.items(), start=1)
            )

            await ctx.send(embed=embed)
        finally:
            task.cancel()

    def safe_chan(self, member: discord.Member, channels: list[discord.VoiceChannel]) -> discord.VoiceChannel | None:
        """ """
        random.shuffle(channels)
        for channel in channels:
            if channel.permissions_for(member).connect:
                return channel
        return None

    @commands.command(hidden=True, name="scatter", aliases=["scattertheweak"])
    @checks.has_guild_permissions(administrator=True)
    async def scatter(self, ctx: GuildContext, voice_channel: discord.VoiceChannel | None = None) -> None:
        assert isinstance(ctx.author, discord.Member)
        if voice_channel:
            channel = voice_channel
        else:
            if ctx.author.voice:
                channel = ctx.author.voice.channel
            else:
                channel = None

        if channel is None:
            await ctx.send("No voice channel.")
            return

        members = channel.members
        for member in members:
            target = self.safe_chan(member, ctx.guild.voice_channels)
            if target is None:
                continue
            await member.move_to(target)

    @commands.command(hidden=True, name="snap")
    @checks.has_guild_permissions(administrator=True)
    async def snap(self, ctx: GuildContext) -> None:
        members: list[discord.Member] = []
        for vc in ctx.guild.voice_channels:
            members.extend(vc.members)

        upper = math.ceil(len(members) / 2)
        choices = random.choices(members, k=upper)

        for m in choices:
            await m.move_to(None)

    def _handle_image(self, buffer: io.BytesIO) -> io.BytesIO:
        output_buffer = io.BytesIO()
        with Image.open(buffer) as image, Image.open(resolved_path) as bricks:
            new_size = legofy.get_new_size(image, bricks, None)
            image = image.resize(new_size, Image.ANTIALIAS)

            pil_image = legofy.make_lego_image(image, bricks)
            pil_image.save(output_buffer, "png")

            output_buffer.seek(0)

        return output_buffer

    @commands.command(name="lego")
    async def lego_command(
        self, ctx: Context, *, target: discord.User | discord.Emoji | discord.PartialEmoji | str | None
    ) -> None:
        if target is None:
            if attachments := (
                ctx.message.attachments
                or (
                    (ctx.replied_reference and ctx.replied_reference.cached_message)
                    and ctx.replied_reference.cached_message.attachments
                )
            ):
                bytes_ = await attachments[0].read()
            else:
                bytes_ = await ctx.author.display_avatar.read()
        elif isinstance(target, (discord.User, discord.ClientUser)):
            bytes_ = await target.display_avatar.read()
        elif isinstance(target, discord.Emoji):
            bytes_ = await target.read()
        elif isinstance(target, discord.PartialEmoji):
            if target.is_unicode_emoji():
                raise commands.BadArgument("The passed emoji must be a custom emoji, sorry.")
            bytes_ = await target.read()
        elif isinstance(target, str):
            try:
                async with ctx.bot.session.get(target) as resp:
                    bytes_ = await resp.read()
                    try:
                        discord.utils._get_mime_type_for_image(bytes_)
                    except:
                        pass
            except aiohttp.ClientError:
                raise commands.BadArgument("Sorry, this url doesn't appear to be valid.")

        else:
            bytes_ = await ctx.author.display_avatar.read()

        buffer = io.BytesIO(bytes_)
        buffer.seek(0)

        message = await ctx.send("Generating image...")

        async with ctx.typing():
            func_ = functools.partial(self._handle_image, buffer)
            output_buffer = await asyncio.to_thread(func_)

        file_ = discord.File(output_buffer, filename="lego.png")

        await message.edit(content=None, attachments=[file_])

    @lego_command.error
    async def lego_error_handler(self, ctx: Context, error: commands.CommandError):
        error = getattr(error, "original", error)

        if isinstance(error, commands.BadArgument):
            await ctx.send(str(error))
            return
        await ctx.send("Something else broke, Umbra will fix it.")


async def setup(bot: Mipha) -> None:
    await bot.add_cog(Fun(bot))
