"""
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at http://mozilla.org/MPL/2.0/.
"""

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
from discord.ext import commands
from discord.http import json_or_text

from utilities import checks
from utilities.cache import cache
from utilities.formats import to_codeblock
from utilities.paginator import RoboPages, SimpleListSource

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from bot import Mipha
    from utilities._types.danbooru import DanbooruPayload
    from utilities._types.gelbooru import GelbooruPayload, GelbooruPostPayload
    from utilities._types.uploader import AudioPost
    from utilities.context import Context, GuildContext

SIX_DIGITS = re.compile(r"\{(\d{1,6})\}")
MEDIA_PATTERN = re.compile(
    r"(https?://(?P<host_url>media\.soundgasm\.net|media\d\.vocaroo\.com)(?:\/sounds|\/mp3)\/(?P<media>[a-zA-Z0-9]+)?\.?(?P<ext>m4a|mp3)?)"
)
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

    def __init__(self, tags: set[str]) -> None:
        self.blacklisted_tags: set[str] = tags
        self.blacklist_tags_fmt: str = " | ".join(tags)
        super().__init__("Bad Booru tags.")

    def __str__(self) -> str:
        return f"Found blacklisted tags in query: `{self.blacklist_tags_fmt}`."


class BooruConfig:
    """Config object per guild."""

    __slots__ = (
        "guild_id",
        "bot",
        "record",
        "blacklist",
        "auto_six_digits",
    )

    def __init__(self, *, guild_id: int, bot: Mipha, record: _LewdTableData | None = None) -> None:
        self.guild_id: int = guild_id
        self.bot: Mipha = bot
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
        self.image: bool = payload["width"] != 0
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
        self.image: bool = self.ext in ("png", "jpg", "jpeg", "gif")
        self.video: bool = self.ext in ("mp4", "gifv", "webm")
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
    def __init__(self, bot: Mipha, /) -> None:
        self.bot: Mipha = bot
        self.gelbooru_config = BooruData(
            aiohttp.BasicAuth(bot.config.GELBOORU_AUTH["user_id"], bot.config.DANBOORU_AUTH["api_key"]),
            "https://gelbooru.com/index.php?page=dapi&s=post&q=index",
        )
        self.danbooru_config = BooruData(
            aiohttp.BasicAuth(bot.config.DANBOORU_AUTH["user_id"], bot.config.DANBOORU_AUTH["api_key"]),
            "https://danbooru.donmai.us/posts.json",
        )

    async def cog_check(self, ctx: Context) -> bool:
        return await ctx.bot.is_owner(ctx.author)

    async def cog_command_error(self, ctx: Context, error: commands.CommandError) -> None:
        error = getattr(error, "original", error)

        if isinstance(error, (BlacklistedBooru, commands.BadArgument)):
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
        connection: asyncpg.Connection | asyncpg.Pool | None = None,
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

    async def _cache_soundgasm(
        self, url: re.Match[str], /, *, title: str | None, author: str | None
    ) -> tuple[bytes, str, int]:
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
            data: AudioPost | str = await json_or_text(resp)  # type: ignore # this is weird dict narrowing

        if isinstance(data, str):
            raise ValueError(f"Returned response is not a dict:\n{data}")

        return audio, data["url"], data["size"]

    async def _cache_vocaroo(self, url: re.Match[str], /, *, media_id: str) -> tuple[bytes, str, int]:
        actual_url = url[0] + media_id
        ext: str = url.groupdict().get("ext") or "m4a"

        self.bot.log_handler.log.info("Vocaroo URL: %s", actual_url)
        headers = {
            "Accept": "audio/webm,audio/ogg,audio/wav,audio/*;q=0.9,application/ogg;q=0.7,video/*;q=0.6,*/*;q=0.5",
            "Accept-Encoding": "Identity",
            "Accept-Language": "en-GB,en,q=0.5",
            "Connection": "Keep-Alive",
            "DNT": "1",
            "Host": "media1.vocaroo.com",
            "Range": "bytes=0-",
            "Referer": "https://vocaroo.com/",
            "Sec-Fetch-Dest": "audio",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Site": "same-site",
            "Sec-GPC": "1",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/112.0",
        }
        async with self.bot.session.get(actual_url, timeout=aiohttp.ClientTimeout(total=1800.00), headers=headers) as resp:
            if not 300 > resp.status > 200:
                self.bot.log_handler.log.info("Vocaroo status code: %s", resp.status)
                raise ValueError("Non 200 response code from vocaroo.")
            audio = await resp.read()

        form_data = aiohttp.FormData()
        form_data.add_field("image", audio, content_type=CONTENT_TYPE_LOOKUP[ext])

        async with self.bot.session.post(
            "https://upload.umbra-is.gay/audio", data=form_data, headers={"Authorization": self.bot.config.IMAGE_HOST_AUTH}
        ) as resp:
            data: AudioPost | str = await json_or_text(resp)  # type: ignore # this is weird dict narrowing

        if isinstance(data, str):
            raise ValueError(f"Returned response is not a dict:\n{data}")

        return audio, data["url"], data["size"]

    def _audio_factory(
        self, match: re.Match[str]
    ) -> Callable[[re.Match[str], str, str | None], Coroutine[Any, Any, tuple[bytes, str, int]]]:
        if match.group("host_url") == "media.soundgasm.net":
            return self._get_soundgasm_data
        return self._get_vocaroo_data

    def _get_soundgasm_data(
        self, match: re.Match[str], data: str, _: str | None
    ) -> Coroutine[Any, Any, tuple[bytes, str, int]]:
        title_match = SOUNDGASM_TITLE_PATTERN.search(data)
        title: str | None = None
        author_match = SOUNDGASM_AUTHOR_PATTERN.search(data)
        author: str | None = None
        if title_match:
            title = re.sub(r"(\s?[\[\(].*?[\]\)]\s?)", "", title_match[1])  # https://regex101.com/r/tFLbEF/2
        if author_match:
            author = author_match[1]

        return self._cache_soundgasm(match, title=title, author=author)

    def _get_vocaroo_data(
        self, match: re.Match[str], data: str, media_id: str | None
    ) -> Coroutine[Any, Any, tuple[bytes, str, int]]:
        if not media_id:
            raise ValueError("Cannot parse Vocaroo media ID from the url.")
        return self._cache_vocaroo(match, media_id=media_id)

    @commands.command(aliases=["sg"])
    @commands.is_owner()
    async def soundgasm(self, ctx: Context, *, url: str) -> None:
        """
        For archiving soundgasm links...
        """
        await ctx.typing()
        async with ctx.bot.session.get(url) as response:
            data = await response.text()

        if found_url := MEDIA_PATTERN.search(data):
            callable_ = self._audio_factory(found_url)
            media_id = None
            if found_url["host_url"] != "media.soundgasm.net":
                media_id = url.rsplit("/")[-1]
            audio, cache_url, size = await callable_(found_url, data, media_id)
        else:
            await ctx.send("Can't find the content within the main url.")
            return

        target_size = (ctx.guild and ctx.guild.filesize_limit) or 1024 * 1024 * 8
        if size >= target_size:
            await ctx.send(f"The file is too large, have the url: {cache_url}")
            return

        fmt = BytesIO(audio)
        fmt.seek(0)

        await ctx.send(file=discord.File(fmt, filename="you_horny_fuck.m4a"))

    async def _play_asmr(self, url: str, /, *, ctx: GuildContext, v_client: discord.VoiceClient | None) -> None:
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
        query = """
                SELECT *
                FROM audio
                TABLESAMPLE BERNOULLI (20);
                """

        conn: asyncpg.Connection = await asyncpg.connect(
            host=self.bot.config.POSTGRES_AUDIO_DSN["host"],
            port=self.bot.config.POSTGRES_AUDIO_DSN["port"],
            user=self.bot.config.POSTGRES_AUDIO_DSN["user"],
            password=self.bot.config.POSTGRES_AUDIO_DSN["password"],
            database=self.bot.config.POSTGRES_AUDIO_DSN["database"],
        )

        rows = await conn.fetch(query)
        await conn.close()
        if not rows:
            await ctx.send("No more asmr.")
            return
        row = random.choice(rows)

        url = f"https://audio.saikoro.moe/{row['filename']}"

        await ctx.send(f"You're listening to: **{row['title']}**\nBy: **{row['soundgasm_author']}**\n{url}")

        if ctx.guild:
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
        current_config = await self.get_booru_config(id_)

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
        current_config = await self.get_booru_config(id_)

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
    async def lewd(self, ctx: GuildContext) -> None:
        """Naughty commands! Please see the subcommands."""
        if not ctx.invoked_subcommand:
            return await ctx.send_help(ctx.command)

    @lewd.group(invoke_without_command=True)
    @checks.has_permissions(manage_messages=True)
    async def blacklist(self, ctx: GuildContext) -> None:
        """Blacklist management for booru command."""
        if not ctx.invoked_subcommand:
            config = await self.get_booru_config(ctx.guild.id)
            fmt = "\n".join(config.blacklist) if config.blacklist else "No blacklist recorded."
            embed = discord.Embed(
                description=to_codeblock(fmt, language=""),
                colour=discord.Colour.dark_magenta(),
            )
            await ctx.send(embed=embed, delete_after=6.0)

    @blacklist.command()
    @checks.has_permissions(manage_messages=True)
    async def add(self, ctx: GuildContext, *tags: str) -> None:
        """Add an item to the blacklist."""
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
    async def remove(self, ctx: GuildContext, *tags: str) -> None:
        """Remove an item from the blacklist."""
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


async def setup(bot: Mipha) -> None:
    await bot.add_cog(Lewd(bot))
