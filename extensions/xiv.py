from __future__ import annotations

import datetime
import re
import zoneinfo
from typing import TYPE_CHECKING, ClassVar

import discord
from discord.ext import commands, tasks
from discord.utils import format_dt

from utilities._types.xiv.reddit.kaiyoko import TopLevelListingResponse
from utilities.context import Context
from utilities.time import ordinal


if TYPE_CHECKING:
    from bot import Kukiko

IRLS_GUILD_ID = 174702278673039360
FUNHOUSE_ID = 174702278673039360
XIV_ROLE_ID = 970754264643293264
FASHION_REPORT_PATTERN = re.compile(
    r"Fashion Report - Full Details - For Week of (?P<date>[0-9]{1,2}/[0-9]{1,2}/[0-9]{4}) \(Week (?P<week_num>[0-9]{3})\)"
)
FASHION_REPORT_START = datetime.datetime(
    year=2018,
    month=1,
    day=27,
    hour=8,
    minute=0,
    second=0,
    microsecond=0,
    tzinfo=datetime.timezone.utc,
)


class XIV(commands.Cog):
    DAILIES: ClassVar[list[str]] = ["Beast Tribe", "Duty Roulettes", "Hunt Marks", "Mini Cactpot", "Levequests"]
    WEEKLIES: ClassVar[list[str]] = [
        "Custom Delivery",
        "Doman Enclave",
        "Wondrous Tails",
        "Hunt Marks",
        "Raid Lockouts",
        "Challenge Log",
        "Masked Carnivale",
        "Squadron Missions",
        "Currency Limits",
    ]

    def __init__(self, bot: Kukiko) -> None:
        self.bot: Kukiko = bot
        self.daily_reset.start()
        self.weekly_reset.start()
        self.fashion_report_loop.start()
        self.jumbo_cactpot.start()

    async def cog_unload(self) -> None:
        self.daily_reset.cancel()
        self.weekly_reset.cancel()
        self.fashion_report_loop.cancel()
        self.jumbo_cactpot.cancel()

    def weeks_since_start(self, dt: datetime.datetime) -> int:
        td = dt - FASHION_REPORT_START

        seconds = round(td.total_seconds())
        weeks, _ = divmod(seconds, 60 * 60 * 24 * 7)

        return weeks

    def humanify_delta(self, *, td: datetime.timedelta, format: str) -> str:
        seconds = round(td.total_seconds())

        days, seconds = divmod(seconds, 60 * 60 * 24)
        hours, seconds = divmod(seconds, 60 * 60)
        minutes, seconds = divmod(seconds, 60)

        return f"{format.title()} in {days} days, {hours} hours, {minutes} minutes and {seconds} seconds."

    async def get_kaiyoko_submissions(self) -> TopLevelListingResponse:
        headers = {"User-Agent": "Kukiko Discord Bot (by /u/AbstractUmbra)"}
        async with self.bot.session.get("https://reddit.com/user/kaiyoko/submitted.json", headers=headers) as resp:
            data: TopLevelListingResponse = await resp.json()

        return data

    async def filter_submissions(self) -> tuple[str, str, str]:
        submissions = await self.get_kaiyoko_submissions()

        for submission in submissions["data"]["children"]:
            if match := FASHION_REPORT_PATTERN.search(submission["data"]["title"]):
                now = datetime.datetime.now(datetime.timezone.utc)
                if not self.weeks_since_start(now) == int(match["week_num"]):
                    continue

                created = datetime.datetime.fromtimestamp(submission["data"]["created_utc"], tz=datetime.timezone.utc)
                if (now - created) > datetime.timedelta(days=7):
                    continue

                if 1 < now.weekday() < 5:
                    delta = datetime.timedelta((4 - now.weekday()) % 7)
                    fmt = "Available"
                else:
                    delta = datetime.timedelta((1 - now.weekday()) % 7)
                    fmt = "Resets"

                upcoming_event = now + delta
                upcoming_event = upcoming_event.replace(hour=8, minute=0, second=0, microsecond=0)
                reset_str = self.humanify_delta(td=(upcoming_event - now), format=fmt)

                return (
                    f"Fashion Report details for week of {match['date']} (Week {match['week_num']})",
                    reset_str,
                    submission["data"]["url"],
                )

        raise ValueError("Unabled to fetch the reddit post details.")

    async def _get_channel(self) -> discord.TextChannel:
        guild = self.bot.get_guild(IRLS_GUILD_ID) or await self.bot.fetch_guild(IRLS_GUILD_ID)
        channel = guild.get_channel(FUNHOUSE_ID)
        assert isinstance(channel, discord.TextChannel)

        return channel

    async def _gen_fashion_embed(self) -> discord.Embed:
        prose, reset, url = await self.filter_submissions()

        embed = discord.Embed(title=prose, url=url)
        embed.description = reset
        embed.set_image(url=url)

        return embed

    @commands.command(name="fashion-report", aliases=["fr"])
    async def fashion_report(self, ctx: Context) -> None:
        """Fetch the latest fashion report data from /u/Kaiyoko."""
        embed = await self._gen_fashion_embed()

        await ctx.send(embed=embed)

    @commands.command(name="servertime", aliases=["st", "ST"])
    async def server_time(self, ctx: Context) -> None:
        """Shows your local time against the Chaos datacenter server time."""
        my_now = datetime.datetime.now(zoneinfo.ZoneInfo("Europe/London"))
        server_now = datetime.datetime.now(datetime.timezone.utc)

        ord_ = ordinal(server_now.day)

        server_fmt = server_now.strftime(f"%A, {ord_} of %B %Y %H:%M")

        await ctx.send(f"You: {format_dt(my_now, 'F')}\nServer: {server_fmt}")

    @tasks.loop(time=datetime.time(hour=14, minute=45, tzinfo=datetime.timezone.utc))
    async def daily_reset(self) -> None:
        channel = await self._get_channel()

        embed = discord.Embed(title="Daily reset in 15 minutes.")
        embed.description = "\n".join(self.DAILIES)
        reset = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)).replace(
            minute=0, second=0, microsecond=0
        )
        embed.timestamp = reset
        embed.set_thumbnail(
            url="https://media.discordapp.net/attachments/872373121292853248/991352363577250003/unknown.png?width=198&height=262",
        )

        fmt = f"Yo <@&{XIV_ROLE_ID}>:"

        await channel.send(fmt, embed=embed, allowed_mentions=discord.AllowedMentions(roles=True))

    @tasks.loop(time=datetime.time(hour=7, minute=45, tzinfo=datetime.timezone.utc))
    async def weekly_reset(self) -> None:
        now = datetime.datetime.now(datetime.timezone.utc)
        if now.weekday() != 1:
            return

        channel = await self._get_channel()

        embed = discord.Embed(title="Weekly reset in 15 minutes.")
        embed.description = "\n".join(self.WEEKLIES)
        reset = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)).replace(
            minute=0, second=0, microsecond=0
        )
        embed.timestamp = reset
        embed.set_thumbnail(
            url="https://media.discordapp.net/attachments/872373121292853248/991352474097168424/unknown.png?width=179&height=267",
        )

        fmt = f"Yo <@&{XIV_ROLE_ID}>:"

        await channel.send(fmt, embed=embed, allowed_mentions=discord.AllowedMentions(roles=True))

    @tasks.loop(time=datetime.time(hour=7, minute=45, tzinfo=datetime.timezone.utc))
    async def fashion_report_loop(self) -> None:
        now = datetime.datetime.now(datetime.timezone.utc)
        if now.weekday() != 4:
            return

        channel = await self._get_channel()

        fmt = f"Yo <@&{XIV_ROLE_ID}>, it's fashion report time in 15 minutes."
        try:
            embed = await self._gen_fashion_embed()
        except ValueError:
            embed = discord.Embed(description="Embed cannot be generated as the post doesn't exist yet.")

        await channel.send(fmt, embed=embed, allowed_mentions=discord.AllowedMentions(roles=True))

    @tasks.loop(time=datetime.time(hour=18, minute=45, tzinfo=datetime.timezone.utc))
    async def jumbo_cactpot(self) -> None:
        now = datetime.datetime.now(datetime.timezone.utc)
        if now.weekday() != 5:
            return

        channel = await self._get_channel()

        fmt = f"Yo <@&{XIV_ROLE_ID}>, it's jumbo cactpot time in 15 minutes."

        await channel.send(fmt, allowed_mentions=discord.AllowedMentions(roles=True))


async def setup(bot: Kukiko) -> None:
    await bot.add_cog(XIV(bot))
