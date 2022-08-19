from __future__ import annotations

import argparse
import datetime
import json
import random
import re
import shlex
from io import BytesIO
from typing import TYPE_CHECKING, Any, NamedTuple, TypedDict

import aiohttp
import asyncpg
import discord
import nhentaio
from discord.ext import commands, tasks

from utilities import checks
from utilities._types.danbooru import DanbooruPayload
from utilities._types.gelbooru import GelbooruPayload, GelbooruPostPayload
from utilities.cache import cache
from utilities.formats import to_codeblock
from utilities.paginator import NHentaiEmbed, RoboPages, SimpleListSource


if TYPE_CHECKING:
    from bot import Kukiko
    from utilities.context import Context

SIX_DIGITS = re.compile(r"\{(\d{1,6})\}")
SOUNDGASM_MEDIA_PATTERN = re.compile(r"(https?://media\.soundgasm\.net\/sounds\/(?P<media>[a-f0-9]+)\.(?P<ext>m4a|mp3))")
SOUNDGASM_TITLE_PATTERN = re.compile(r"\=\"title\"\>(.*?)\<\/div\>")  # https://regex101.com/r/BJyiGM/1
SOUNDGASM_AUTHOR_PATTERN = re.compile(r"\<a href\=\"(?:(?:https?://)?soundgasm\.net\/u\/(?:.*)\")\>(.*)\<\/a>")
CONTENT_TYPE_LOOKUP = {
    "m4a": "audio/mp4",
    "mp3": "audio/mp3",
}
RATING = {"e": "explicit", "q": "questionable", "s": "safe", "g": "general"}
RATING_LOOKUP = {v: k for k, v in RATING.items()}


def _reverse_rating_repl(match: re.Match[str]) -> str:
    key = RATING_LOOKUP.get(match.group(1), "N/A")
    return f"rating:{key}"


class _LewdTableData(TypedDict):
    guild_id: int
    blacklist: list[str]
    auto_six_digits: bool


class BooruData(NamedTuple):
    auth: aiohttp.BasicAuth
    endpoint: str


class BlacklistedBooru(commands.CommandError):
    """Error raised when you request a blacklisted tag."""

    def __init__(self, tags: set[str]):
        self.blacklisted_tags: set[str] = tags
        self.blacklist_tags_fmt: str = " | ".join(tags)
        super().__init__("Bad Booru tags.")

    def __str__(self):
        return f"Found blacklisted tags in query: `{self.blacklist_tags_fmt}`."


class BadNHentaiID(commands.CommandError):
    """Error raised when you request a bad nhentai ID."""

    def __init__(self, hentai_id: int, message: str):
        self.nhentai_id: int = hentai_id
        super().__init__(message)

    def __str__(self):
        return f"Invalid NHentai ID: `{self.nhentai_id}`."


class BooruConfig:
    """Config object per guild."""

    __slots__ = (
        "guild_id",
        "bot",
        "record",
        "blacklist",
        "auto_six_digits",
    )

    def __init__(self, *, guild_id: int, bot: Kukiko, record: _LewdTableData | None = None):
        self.guild_id: int = guild_id
        self.bot: Kukiko = bot
        self.record: _LewdTableData | None = record

        if record:
            self.blacklist = set(record["blacklist"])
            self.auto_six_digits = record["auto_six_digits"]
        else:
            self.blacklist = set()
            self.auto_six_digits = False


class GelbooruEntry:
    """Quick object namespace."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.image: bool = True if (payload["width"] != 0) else False
        self.source: str | None = payload.get("source")
        self.gb_id: str | None = payload.get("id")
        self.rating: str = payload.get("rating", "N/A")
        self.score: int | None = payload.get("score")
        self.url: str | None = payload.get("file_url")
        self.raw_tags: str = payload["tags"]

    @property
    def tags(self) -> list[str]:
        return self.raw_tags.split(" ")


class DanbooruEntry:
    """Quick object namespace."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.ext: str = payload.get("file_ext", "none")
        self.image: bool = True if self.ext in ("png", "jpg", "jpeg", "gif") else False
        self.video: bool = True if self.ext in ("mp4", "gifv", "webm") else False
        self.source: str | None = payload.get("source")
        self.db_id: int | None = payload.get("id")
        self.rating: str | None = RATING.get(payload.get("rating", "fail"))
        self.score: int | None = payload.get("score")
        self.large: bool | None = payload.get("has_large", False)
        self.file_url: str | None = payload.get("file_url")
        self.large_url: str | None = payload.get("large_file_url")
        self.raw_tags: str = payload["tag_string"]

    @property
    def tags(self) -> list[str]:
        return self.raw_tags.split(" ")

    @property
    def url(self) -> str | None:
        return self.large_url if self.large else self.file_url


class Lewd(commands.Cog):
    def __init__(self, bot: Kukiko, /) -> None:
        self.bot: Kukiko = bot
        self.gelbooru_config = BooruData(
            aiohttp.BasicAuth(bot.config.GELBOORU_AUTH["user_id"], bot.config.DANBOORU_AUTH["api_key"]),
            "https://gelbooru.com/index.php?page=dapi&s=post&q=index",
        )
        self.danbooru_config = BooruData(
            aiohttp.BasicAuth(bot.config.DANBOORU_AUTH["user_id"], bot.config.DANBOORU_AUTH["api_key"]),
            "https://danbooru.donmai.us/posts.json",
        )
        self._nhen_queu: set[tuple[int, int, int]] = set()
        self.nhen_deque.start()

    async def cog_command_error(self, ctx: Context, error: commands.CommandError) -> None:
        error = getattr(error, "original", error)

        if isinstance(error, BlacklistedBooru):
            await ctx.send(str(error))
            return
        elif isinstance(error, commands.BadArgument):
            await ctx.send(str(error))
            return
        elif isinstance(error, commands.NSFWChannelRequired):
            await ctx.send(f"{error.channel} is not a horny channel. No lewdie outside lewdie channels!")
            return
        elif isinstance(error, commands.CommandOnCooldown):
            if ctx.author.id == self.bot.owner_id:
                return await ctx.reinvoke()
            await ctx.send(f"Stop being horny. You're on cooldown for {error.retry_after:.02f}s.")
            return

    @cache()
    async def get_booru_config(
        self,
        guild_id: int,
        *,
        connection: asyncpg.Pool | asyncpg.Connection | None = None,
    ) -> BooruConfig:
        connection = connection or self.bot.pool
        query = """
                SELECT *
                FROM lewd_config
                WHERE guild_id = $1;
                """
        record = await connection.fetchrow(query, guild_id)
        return BooruConfig(guild_id=guild_id, bot=self.bot, record=record)

    def _gelbooru_embeds(self, payloads: list[GelbooruPostPayload], config: BooruConfig) -> list[discord.Embed]:
        source: list[discord.Embed] = []

        for payload in payloads:
            tags_ = set(payload["tags"].split())
            if tags_ & config.blacklist:
                continue

            if not payload["image"]:
                continue

            if payload["image"].partition(".")[2] not in ("png", "jpg", "jpeg", "webm", "gif"):
                continue

            created_at = datetime.datetime.strptime(payload["created_at"], "%a %b %d %H:%M:%S %z %Y")
            embed = discord.Embed(colour=discord.Colour.red(), timestamp=created_at.astimezone(datetime.timezone.utc))

            if payload["source"]:
                embed.title = "See Source"
                embed.url = payload["source"]

            embed.set_footer(text=f"Rating: {payload['rating'].title()}")
            embed.set_image(url=payload["file_url"])

            source.append(embed)

        return source

    def _danbooru_embeds(self, payloads: list[DanbooruPayload], config: BooruConfig) -> list[discord.Embed]:
        source: list[discord.Embed] = []

        for payload in payloads:
            tags_ = set(payload["tag_string"].split())
            if tags_ & config.blacklist:
                continue

            if not payload["file_ext"] in ("jpg", "jpeg", "png", "gif", "webm"):
                continue

            created_at = datetime.datetime.fromisoformat(payload["created_at"])
            embed = discord.Embed(colour=discord.Colour.red(), timestamp=created_at.astimezone(datetime.timezone.utc))

            if payload["source"]:
                embed.title = "See Source"
                embed.url = payload["source"]

            embed.set_footer(text=f"Rating: {RATING[payload['rating']].title()}")
            if "file_url" in payload:
                embed.set_image(url=payload["file_url"])
                if payload["has_large"]:
                    embed.description = f"[See the large image.]({payload['large_file_url']})"
            elif payload["pixiv_id"] and payload["source"]:
                embed.set_image(url=payload["source"])
            else:
                continue

            source.append(embed)

        return source

    async def _cache_soundgasm(self, url: re.Match[str], /, *, title: str | None, author: str | None) -> tuple[bytes, str]:
        actual_url = url[0]
        ext = url["ext"]

        async with self.bot.session.get(actual_url, timeout=aiohttp.ClientTimeout(total=1800.00)) as resp:
            audio = await resp.read()

        form_data = aiohttp.FormData()
        form_data.add_field("image", audio, content_type=CONTENT_TYPE_LOOKUP[ext])
        form_data.add_field("title", title if title else "", content_type="text/plain")
        form_data.add_field("soundgasm_author", author if author else "", content_type="text/plain")

        async with self.bot.session.post(
            "https://upload.umbra-is.gay/audio", data=form_data, headers={"Authorization": self.bot.config.IMAGE_HOST_AUTH}
        ) as resp:
            data = await resp.json()

        return audio, data["url"]

    @commands.command()
    @commands.is_owner()
    async def soundgasm(self, ctx: Context, *, url: str) -> None:
        """
        For archiving soundgasm links...
        """
        assert ctx.guild is not None

        await ctx.typing()
        async with ctx.bot.session.get(url) as response:
            data = await response.text()

        if found_url := SOUNDGASM_MEDIA_PATTERN.search(data):
            title_match = SOUNDGASM_TITLE_PATTERN.search(data)
            title: str | None = None
            author_match = SOUNDGASM_AUTHOR_PATTERN.search(data)
            author: str | None = None
            fmt = ""
            if title_match:
                title = re.sub(r"(\s?[\[\(].*?[\]\)]\s?)", "", title_match[1])  # https://regex101.com/r/tFLbEF/2
                fmt += f"{title}\n"
            if author_match:
                author = author_match[1]
                fmt += f"By: **{author}**\n"
            fmt += f"{found_url[1]}"

            await ctx.send(fmt)
            audio, cache_url = await self._cache_soundgasm(found_url, title=title, author=author)
        else:
            await ctx.send("Can't find the content within the main url.")
            return

        fmt = BytesIO(audio)
        fmt.seek(0)

        if len(fmt.read()) >= ctx.guild.filesize_limit:
            await ctx.send(f"The file is too large, have the url: {cache_url}")
            return

        fmt.seek(0)
        await ctx.send(file=discord.File(fmt, filename="you_horny_fuck.m4a"))

    async def _play_asmr(self, url: str, /, *, ctx: Context, v_client: discord.VoiceClient | None) -> None:
        assert isinstance(ctx.author, discord.Member)

        if not ctx.author.voice or not ctx.author.voice.channel:
            return

        v_client = v_client or await ctx.author.voice.channel.connect(cls=discord.VoiceClient)

        if v_client.is_playing():
            v_client.stop()

        audio_ = discord.FFmpegPCMAudio(url)
        transformer_ = discord.PCMVolumeTransformer(audio_)
        v_client.play(transformer_)

    @commands.command()
    @commands.is_owner()
    async def asmr(self, ctx: Context) -> None:
        assert isinstance(ctx.author, discord.Member)

        query = """
                SELECT *
                FROM audio
                TABLESAMPLE BERNOULLI (20);
                """

        conn: asyncpg.Connection = await asyncpg.connect(**ctx.bot.config.POSTGRES_AUDIO_DSN)

        rows = await conn.fetch(query)
        await conn.close()
        if not rows:
            await ctx.send("No more asmr.")
            return
        row = random.choice(rows)

        url = f"https://audio.saikoro.moe/{row['filename']}"

        await ctx.send(f"You're listening to: **{row['title']}**\nBy: **{row['soundgasm_author']}**\n{url}")

        if ctx.guild is not None:
            await self._play_asmr(url, ctx=ctx, v_client=ctx.guild.voice_client)  # type: ignore # did a dummy break voice

    @commands.command(usage="<flags>+ | subcommand", cooldown_after_parsing=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    @commands.max_concurrency(1, commands.BucketType.user, wait=False)
    @commands.is_nsfw()
    async def gelbooru(self, ctx: Context, *, params: str) -> None:
        """Gelbooru command! Access gelbooru searches.
        This command uses a flag style syntax.
        The following options are valid.
        `*` denotes it is a mandatory argument.
        `+t | ++tags`: The tags to search Gelbooru for. `*` (uses logical AND per tag)
        `+l | ++limit`: The maximum amount of posts to show. Cannot be higher than 30.
        `+p | ++pid`: Page ID to search. Handy when posts begin to repeat.
        `+c | ++cid`: Change ID of the post to search for(?)
        Examples:
        ```
        !gelbooru ++tags lemon
            - search for the 'lemon' tag.
            - NOTE: if your tag has a space in it, replace it with '_'
        !gelbooru ++tags melon -rating:explicit
            - search for the 'melon' tag, removing posts marked as 'explicit`
        !gelbooru ++tags apple orange rating:safe ++pid 2
            - Search for the 'apple' AND 'orange' tags, with only 'safe' results, but on Page 2.
            - NOTE: if not enough searches are returned, page 2 will cause an empty response.
        ```
        Possible ratings are: `general`, `sensitive`, `questionable` and `explicit`.
        """
        aiohttp_params = {}
        aiohttp_params.update({"json": 1})
        parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False, prefix_chars="+")
        parser.add_argument("+l", "++limit", type=int, default=40)
        parser.add_argument("+p", "++pid", type=int)
        parser.add_argument("+t", "++tags", nargs="+", required=True)
        parser.add_argument("+c", "++cid", type=int)
        try:
            real_args = parser.parse_args(shlex.split(params))
        except SystemExit as fuck:
            raise commands.BadArgument("Your flags could not be parsed.") from fuck
        except Exception as err:
            await ctx.send(f"Parsing your args failed: {err}")
            return

        id_: int = getattr(ctx.guild, "id", -1)
        current_config = await self.get_booru_config(id_)  # type: ignore # cache is gay

        limit = max(min(0, real_args.limit), 100)
        aiohttp_params.update({"limit": limit})
        if real_args.pid:
            aiohttp_params.update({"pid": real_args.pid})
        if real_args.cid:
            aiohttp_params.update({"cid": real_args.cid})
        lowered_tags = [tag.lower() for tag in real_args.tags]
        tags_set = set(lowered_tags)
        common_elems = tags_set & current_config.blacklist
        if common_elems:
            raise BlacklistedBooru(common_elems)
        aiohttp_params.update({"tags": " ".join(lowered_tags)})

        async with ctx.typing():
            async with self.bot.session.get(
                self.gelbooru_config.endpoint,
                params=aiohttp_params,
                auth=self.gelbooru_config.auth,
            ) as resp:
                data = await resp.text()
                if not data:
                    ctx.command.reset_cooldown(ctx)
                    raise commands.BadArgument("Got an empty response... bad search?")
                json_data: GelbooruPayload = json.loads(data)

            if not json_data:
                ctx.command.reset_cooldown(ctx)
                raise commands.BadArgument("The specified query returned no results.")

            embeds = self._gelbooru_embeds(json_data["post"], current_config)
            if not embeds:
                raise commands.BadArgument("Your search had results but all of them contain blacklisted tags.")
            pages = RoboPages(source=SimpleListSource(embeds[:30]), ctx=ctx)
            await pages.start()

    @commands.command(usage="<flags>+ | subcommand", cooldown_after_parsing=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    @commands.max_concurrency(1, commands.BucketType.user, wait=False)
    @commands.is_nsfw()
    async def danbooru(self, ctx: Context, *, params: str) -> None:
        """Danbooru command. Access danbooru commands.
        This command uses a flag style syntax.
        The following options are valid.
        `*` denotes it is a mandatory argument.
        `+t | ++tags`: The tags to search Gelbooru for. `*` (uses logical AND per tag)
        `+l | ++limit`: The maximum amount of posts to show. Cannot be higher than 30.
        Examples:
        ```
        !gelbooru ++tags lemon
            - search for the 'lemon' tag.
            - NOTE: if your tag has a space in it, replace it with '_'.
        !danbooru ++tags melon -rating:explicit
            - search for the 'melon' tag, removing posts marked as 'explicit`.
        !danbooru ++tags apple orange rating:safe
            - Search for the 'apple' AND 'orange' tags, with only 'safe' results.
        Possible tags are: `general`, `safe`, `questionable` and `explicit`.
        ```
        """
        aiohttp_params = {}
        parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False, prefix_chars="+")
        parser.add_argument("+t", "++tags", nargs="+", required=True)
        parser.add_argument("+l", "++limit", type=int, default=40)
        try:
            real_args = parser.parse_args(shlex.split(params))
        except SystemExit as fuck:
            raise commands.BadArgument("Your flags could not be parsed.") from fuck
        except Exception as err:
            await ctx.send(f"Parsing your args failed: {err}.")
            return

        id_: int = getattr(ctx.guild, "id", -1)
        current_config = await self.get_booru_config(id_)  # type: ignore # cache is gay

        limit = max(min(0, real_args.limit), 100)
        aiohttp_params.update({"limit": limit})
        lowered_tags = [
            re.sub(r"rating\:(safe|questionable|explicit)", _reverse_rating_repl, tag.lower()) for tag in real_args.tags
        ]
        tags = set(lowered_tags)
        common_elems = tags & current_config.blacklist
        if common_elems:
            raise BlacklistedBooru(common_elems)
        aiohttp_params.update({"tags": " ".join(tags)})

        async with ctx.typing():
            async with self.bot.session.get(
                self.danbooru_config.endpoint,
                params=aiohttp_params,
                auth=self.danbooru_config.auth,
            ) as resp:
                data = await resp.text()
                if not data:
                    ctx.command.reset_cooldown(ctx)
                    raise commands.BadArgument("Got an empty response... bad search?")
                json_data: list[DanbooruPayload] = json.loads(data)

            if not json_data:
                ctx.command.reset_cooldown(ctx)
                raise commands.BadArgument("The specified query returned no results.")

            embeds = self._danbooru_embeds(json_data, current_config)
            if not embeds:
                fmt = "Your search had results but all of them contained blacklisted tags"
                if "loli" in lowered_tags:
                    fmt += "\nPlease note that Danbooru does not support 'loli'."
                raise commands.BadArgument(fmt)

            pages = RoboPages(source=SimpleListSource(embeds[:30]), ctx=ctx)
            await pages.start()

    @commands.group(invoke_without_command=True, name="lewd", aliases=["booru", "naughty"])
    @checks.has_permissions(manage_messages=True)
    @commands.is_nsfw()
    async def lewd(self, ctx: Context) -> None:
        """Naughty commands! Please see the subcommands."""
        if not ctx.invoked_subcommand:
            return await ctx.send_help(ctx.command)

    @lewd.group(invoke_without_command=True)
    @checks.has_permissions(manage_messages=True)
    async def blacklist(self, ctx: Context) -> None:
        """Blacklist management for booru command and nhentai auto-six-digits."""
        if not ctx.invoked_subcommand:
            config = await self.get_booru_config(ctx.guild.id)  # type: ignore # cache is gay
            if config.blacklist:
                fmt = "\n".join(config.blacklist)
            else:
                fmt = "No blacklist recorded."
            embed = discord.Embed(
                description=to_codeblock(fmt, language=""),
                colour=discord.Colour.dark_magenta(),
            )
            await ctx.send(embed=embed, delete_after=6.0)

    @blacklist.command()
    @checks.has_permissions(manage_messages=True)
    async def add(self, ctx: Context, *tags: str):
        """Add an item to the blacklist."""
        assert ctx.guild is not None

        query = """
                --begin-sql
                INSERT INTO lewd_config (guild_id, blacklist)
                VALUES ($1, $2)
                ON CONFLICT (guild_id)
                DO UPDATE SET blacklist = lewd_config.blacklist || $2;
                """
        iterable = [(ctx.guild.id, [tag.lower()]) for tag in tags]
        await self.bot.pool.executemany(query, iterable)
        self.get_booru_config.invalidate(self, ctx.guild.id)
        await ctx.message.add_reaction(ctx.tick(True))

    @blacklist.command()
    @checks.has_permissions(manage_messages=True)
    async def remove(self, ctx: Context, *tags: str):
        """Remove an item from the blacklist."""
        assert ctx.guild is not None

        query = """
                --begin-sql
                UPDATE lewd_config
                SET blacklist = array_remove(lewd_config.blacklist, $2)
                WHERE guild_id = $1;
                """
        iterable = [(ctx.guild.id, tag) for tag in tags]
        await self.bot.pool.executemany(query, iterable)
        self.get_booru_config.invalidate(self, ctx.guild.id)
        await ctx.message.add_reaction(ctx.tick(True))

    @commands.group(invoke_without_command=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    @commands.max_concurrency(1, commands.BucketType.user, wait=False)
    @commands.is_nsfw()
    async def nhentai(self, ctx, hentai_id: int):
        """Naughty. Return info, the cover and links to an nhentai gallery."""
        gallery = await self.bot.h_client.fetch_gallery(hentai_id)

        if not gallery:
            raise BadNHentaiID(hentai_id, "Doesn't seem to be a valid ID.")

        embed = NHentaiEmbed.from_gallery(gallery)
        await ctx.send(embed=embed)

    async def _create_empty_config(self, ctx: Context, /) -> BooruConfig:
        assert ctx.guild is not None

        query = """
                INSERT INTO lewd_config (guild_id, blacklist, auto_six_digits)
                VALUES ($1, $2, $3)
                RETURNING *;
                """
        await self.bot.pool.fetchrow(query, ctx.guild.id, [], False)
        return BooruConfig(guild_id=ctx.guild.id, bot=ctx.bot, record=None)

    @nhentai.command(name="toggle")
    @checks.has_guild_permissions(manage_messages=True)
    async def nhentai_toggle(self, ctx: Context) -> None:
        """
        This command will toggle the auto parsing of NHentai IDs in messages in the form of:-
        `{123456}`
        Criteria for parsing:
        - Cannot be done in DM.
        - Must be in an NSFW channel.
        - Must be a user or bot that posts it, no webhooks.
        - If the ID does not match a gallery, it will not respond.
        Toggle will do as it says, switch between True and False. Only when it is True will it parse and respond.
        The reaction added will tell you if it is on (check mark), or off (cross).
        """
        assert ctx.guild is not None

        config: BooruConfig = await self.get_booru_config(ctx.guild.id)  # type: ignore # cache is gay
        if not config:
            await ctx.send("No recorded config for this guild. Creating one.")
            self.get_booru_config.invalidate(self, ctx.guild.id)
            config: BooruConfig = await self._create_empty_config(ctx)

        enabled = config.auto_six_digits

        await ctx.message.add_reaction(ctx.tick(not enabled))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        assert not isinstance(message.channel, (discord.PartialMessageable, discord.GroupChannel))
        if not message.guild or message.webhook_id:
            return

        if not isinstance(message.channel, discord.DMChannel) and not message.channel.is_nsfw():
            return

        config: BooruConfig = await self.get_booru_config(message.guild.id)  # type: ignore # cache is gay
        if config.auto_six_digits is False:
            return

        if not (match := SIX_DIGITS.match(message.content)):
            return

        digits = int(match[1])

        try:
            gallery: nhentaio.Gallery | None = await self.bot.h_client.fetch_gallery(digits)
        except nhentaio.NHentaiError:
            await message.channel.send(
                "I would have given you the cum provocation but NHentai is down. I queued it and will try again."
            )
            self._nhen_queu.add((message.author.id, message.channel.id, digits))
            return

        if not gallery:
            return

        tags = set([tag.name for tag in gallery.tags])
        if bl := config.blacklist & tags:
            clean = "|".join(bl)
            await message.reply(f"This gallery has blacklisted tags: `{clean}`.", delete_after=5)
            return

        embed = NHentaiEmbed.from_gallery(gallery)
        await message.reply(embed=embed)

    @tasks.loop(minutes=20)
    async def nhen_deque(self) -> None:
        for author, channel_id, digits in self._nhen_queu:
            try:
                gallery = await self.bot.h_client.fetch_gallery(digits)
            except nhentaio.NHentaiError:
                continue
            if gallery is None:
                self._nhen_queu.remove((author, channel_id, digits))
                return

            fmt = f"Hey <@{author}>, I finally got that gallery:-"
            embed = NHentaiEmbed.from_gallery(gallery)
            channel = self.bot.get_channel(channel_id)
            self._nhen_queu.remove((author, channel_id, digits))
            if channel is None:
                return
            assert isinstance(channel, discord.TextChannel)

            await channel.send(fmt, embed=embed)


async def setup(bot: Kukiko) -> None:
    await bot.add_cog(Lewd(bot))
