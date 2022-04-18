"""
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at http://mozilla.org/MPL/2.0/.
"""
import datetime
import re
import zoneinfo
from typing import Any, Literal, Sequence, Type, TypedDict

import yarl
from discord.ext import commands
from typing_extensions import NotRequired, Self

from utilities.context import Context


class DucklingNormalised(TypedDict):
    unit: Literal["second"]
    value: int


class DucklingResponseValue(TypedDict):
    normalized: DucklingNormalised
    type: Literal["value"]
    unit: str
    value: NotRequired[str]
    minute: NotRequired[int]
    hour: NotRequired[int]
    second: NotRequired[int]
    day: NotRequired[int]
    week: NotRequired[int]
    hour: NotRequired[int]


class DucklingResponse(TypedDict):
    body: str
    dim: Literal["duration", "time"]
    end: int
    start: int
    latent: bool
    value: DucklingResponseValue


class MemeDict(dict):
    def __getitem__(self, k: Sequence[Any]) -> Any:
        for key in self:
            if k in key:
                return super().__getitem__(key)
        raise KeyError(k)


class RedditMediaURL:
    VALID_PATH = re.compile(r"/r/[A-Za-z0-9_]+/comments/[A-Za-z0-9]+(?:/.+)?")

    def __init__(self, url: yarl.URL) -> None:
        self.url = url
        self.filename = url.parts[1] + ".mp4"

    @classmethod
    async def convert(cls: Type[Self], ctx: Context, argument: str) -> Self:
        try:
            url = yarl.URL(argument)
        except Exception:
            raise commands.BadArgument("Not a valid URL.")

        headers = {"User-Agent": "Discord:Kukiko:v1.0 (by /u/AbstractUmbra)"}
        await ctx.trigger_typing()
        if url.host == "v.redd.it":
            # have to do a request to fetch the 'main' URL.
            async with ctx.session.get(url, headers=headers) as resp:
                url = resp.url

        if url.host is None:
            raise commands.BadArgument("Not a valid v.reddit url.")

        is_valid_path = url.host.endswith(".reddit.com") and cls.VALID_PATH.match(url.path)
        if not is_valid_path:
            raise commands.BadArgument("Not a reddit URL.")

        # Now we go the long way
        async with ctx.session.get(url / ".json", headers=headers) as resp:
            if resp.status != 200:
                raise commands.BadArgument(f"Reddit API failed with {resp.status}.")

            data = await resp.json()
            try:
                submission = data[0]["data"]["children"][0]["data"]
            except (KeyError, TypeError, IndexError):
                raise commands.BadArgument("Could not fetch submission.")

            try:
                media = submission["media"]["reddit_video"]
            except (KeyError, TypeError):
                try:
                    # maybe it's a cross post
                    crosspost = submission["crosspost_parent_list"][0]
                    media = crosspost["media"]["reddit_video"]
                except (KeyError, TypeError, IndexError):
                    raise commands.BadArgument("Could not fetch media information.")

            try:
                fallback_url = yarl.URL(media["fallback_url"])
            except KeyError:
                raise commands.BadArgument("Could not fetch fall back URL.")

            return cls(fallback_url)


class DatetimeConverter(commands.Converter[datetime.datetime]):
    @staticmethod
    async def get_timezone(ctx: Context) -> zoneinfo.ZoneInfo | None:
        assert ctx.guild is not None

        row = await ctx.bot.pool.fetchval(
            "SELECT tz FROM tz_store WHERE user_id = $1 and $2 = ANY(guild_ids);", ctx.author.id, ctx.guild.id
        )
        if row:
            row = zoneinfo.ZoneInfo(row)
            return row

    @classmethod
    async def parse(
        cls,
        argument: str,
        /,
        *,
        ctx: Context,
        timezone: datetime.tzinfo | None = datetime.timezone.utc,
        now: datetime.datetime | None = None,
    ) -> list[tuple[datetime.datetime, int, int]]:
        now = now or datetime.datetime.now(datetime.timezone.utc)

        times = []

        async with ctx.bot.session.post(
            "http://127.0.0.1:7731/parse",
            data={
                "locale": "en_US",  # Todo: locale based on tz?
                "text": argument,
                "dims": '["time", "duration"]',
                "tz": str(timezone),
            },
        ) as response:
            data: list[DucklingResponse] = await response.json()

            for time in data:
                if time["dim"] == "time" and "value" in time["value"]:
                    times.append(
                        (
                            datetime.datetime.fromisoformat(time["value"]["value"]),
                            time["start"],
                            time["end"],
                        )
                    )
                elif time["dim"] == "duration":
                    times.append(
                        (
                            datetime.datetime.now(datetime.timezone.utc)
                            + datetime.timedelta(seconds=time["value"]["normalized"]["value"]),
                            time["start"],
                            time["end"],
                        )
                    )

        return times

    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> datetime.datetime:

        timezone = await cls.get_timezone(ctx)
        now = ctx.message.created_at.astimezone(tz=timezone)

        parsed_times = await cls.parse(argument, ctx=ctx, timezone=timezone, now=now)

        if len(parsed_times) == 0:
            raise commands.BadArgument("Could not parse time.")
        elif len(parsed_times) > 1:
            ...  # TODO: Raise on too many?

        return parsed_times[0][0]


class WhenAndWhatConverter(commands.Converter[tuple[datetime.datetime, str]]):
    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> tuple[datetime.datetime, str]:
        timezone = await DatetimeConverter.get_timezone(ctx)
        now = ctx.message.created_at.astimezone(tz=timezone)

        # Strip some common stuff
        for prefix in ("me to ", "me in ", "me at ", "me that "):
            if argument.startswith(prefix):
                argument = argument[len(prefix) :]
                break

        for suffix in ("from now",):
            if argument.endswith(suffix):
                argument = argument[: -len(suffix)]

        argument = argument.strip()

        # Determine the date argument
        parsed_times = await DatetimeConverter.parse(argument, ctx=ctx, timezone=timezone, now=now)

        if len(parsed_times) == 0:
            raise commands.BadArgument("Could not parse time.")
        elif len(parsed_times) > 1:
            ...  # TODO: Raise on too many?

        when, begin, end = parsed_times[0]

        if begin != 0 and end != len(argument):
            raise commands.BadArgument("Could not distinguish time from argument.")

        if begin == 0:
            what = argument[end + 1 :].lstrip(" ,.!:;")
        else:
            what = argument[:begin].strip()

        for prefix in ("to ",):
            if what.startswith(prefix):
                what = what[len(prefix) :]

        return (when, what)
