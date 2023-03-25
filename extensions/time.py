"""
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at http://mozilla.org/MPL/2.0/.
"""

from __future__ import annotations

import asyncio
import collections
import datetime
import difflib
import zoneinfo
from typing import TYPE_CHECKING, Literal, TypedDict, overload

import discord
from discord import app_commands
from discord.ext import commands

from utilities import time
from utilities.context import Context, Interaction
from utilities.formats import random_pastel_colour
from utilities.fuzzy import extract
from utilities.paginator import RoboPages, SimpleListSource


if TYPE_CHECKING:
    from bot import Mipha

AVAILABLE_TIMEZONES = {zone.replace("_", " "): zone for zone in zoneinfo.available_timezones()}


class TimezoneRecord(TypedDict):
    user_id: int
    tz: str


class TimezoneConverter(commands.Converter[zoneinfo.ZoneInfo]):
    async def convert(self, ctx: Context, argument: str) -> zoneinfo.ZoneInfo:
        query = extract(query=argument.lower(), choices=AVAILABLE_TIMEZONES.values(), limit=5)
        if argument.lower() not in {timezone.lower() for timezone in AVAILABLE_TIMEZONES}:
            matches = "\n".join([f"`{index}.` {match[0]}" for index, match in enumerate(query, start=1)])
            question = await ctx.send(f"That was not a recognised timezone. Maybe you meant one of these?\n{matches}")

            def check(message: discord.Message) -> bool:
                return (
                    message.author == ctx.author
                    and message.channel == ctx.channel
                    and message.content.removesuffix(".").isdigit()
                    and 1 >= int(message.content.removesuffix(".")) <= 5
                )

            try:
                result: discord.Message = await ctx.bot.wait_for("message", check=check, timeout=30)
            except asyncio.TimeoutError:
                await question.delete()
                raise commands.BadArgument("No valid timezone given or selected.")

            return zoneinfo.ZoneInfo(query[int(result.content) - 1][0])

        return zoneinfo.ZoneInfo(AVAILABLE_TIMEZONES[argument])


class TimezoneSource(SimpleListSource):
    def format_page(self, _: RoboPages, entries: list[tuple[str, str, datetime.timedelta]]) -> discord.Embed:
        embed = discord.Embed(title="Dannyware Timezones!", colour=random_pastel_colour())
        tz_dict = collections.defaultdict(list)

        def to_hour(td: datetime.timedelta) -> int:
            seconds = round(td.total_seconds())
            return seconds // (60 * 60)

        for member_str, dt_string, offset in entries:
            if not offset:
                offset = datetime.timedelta(0)

            houred_offset = to_hour(offset)
            tz_dict[houred_offset].append(f"{member_str}: {dt_string}")

        for key, value in sorted(tz_dict.items()):
            fmt = ""
            for idx, member in enumerate(value, start=1):
                fmt += f"{idx}. {member}"
            name = "UTC" if key == 0 else f"{key} hours from UTC"
            embed.add_field(name=name, value=fmt, inline=False)

        embed.timestamp = discord.utils.utcnow()

        return embed


class Time(commands.Cog):
    """Time cog for fun time stuff."""

    def __init__(self, bot: Mipha) -> None:
        self.bot: Mipha = bot
        self._timezone_cache: list[app_commands.Choice[str]] = [
            app_commands.Choice(name=name, value=val) for name, val in AVAILABLE_TIMEZONES.items()
        ]

    async def cog_command_error(self, ctx: Context, error: commands.CommandError) -> None:
        """Error handling for Time.py."""
        error = getattr(error, "original", error)
        if isinstance(error, commands.BadArgument):
            await ctx.send(str(error))

    def _gen_tz_embeds(self, requester: str, iterable: list[str]) -> list[discord.Embed]:
        embeds = []

        for item in iterable:
            embed = discord.Embed(title="Timezone lists", colour=discord.Colour.green())
            embed.description = "\n".join(item)
            fmt = f"Page {iterable.index(item)+1}/{len(iterable)}"
            embed.set_footer(text=f"{fmt} | Requested by: {requester}")
            embeds.append(embed)
        return embeds

    @overload
    def _curr_tz_time(self, curr_timezone: zoneinfo.ZoneInfo, *, ret_datetime: Literal[True]) -> datetime.datetime:
        ...

    @overload
    def _curr_tz_time(self, curr_timezone: zoneinfo.ZoneInfo, *, ret_datetime: Literal[False]) -> str:
        ...

    def _curr_tz_time(self, curr_timezone: zoneinfo.ZoneInfo, *, ret_datetime: bool = False) -> datetime.datetime | str:
        """We assume it's a good tz here."""
        dt_obj = datetime.datetime.now(curr_timezone)
        if ret_datetime:
            return dt_obj
        return time.hf_time(dt_obj)

    @commands.hybrid_group(invoke_without_command=True, fallback="get", aliases=["time", "tz"])
    @app_commands.describe(member="The member to fetch the timezone of. Yours is shown if blank.")
    async def timezone(self, ctx: Context, *, member: discord.Member | None = None) -> None:
        """Get a member's stored timezone."""
        if ctx.invoked_subcommand:
            pass

        full_member = member or ctx.author

        async with ctx.typing(ephemeral=False):
            query = """SELECT *
                    FROM tz_store
                    WHERE user_id = $1;
                    """
            result = await self.bot.pool.fetchrow(query, full_member.id)

            if not result:
                await ctx.send(f"No timezone for {full_member} set.")
                return

            member_timezone = result["tz"]
            tz = await TimezoneConverter().convert(ctx, member_timezone.replace("_", " "))
            current_time = self._curr_tz_time(tz, ret_datetime=False)
            embed = discord.Embed(title=f"Time for {full_member}", description=f"```\n{current_time}\n```")
            embed.set_footer(text=member_timezone)
            embed.timestamp = datetime.datetime.now(datetime.timezone.utc)

        await ctx.send(embed=embed)

    @timezone.command(name="set")
    async def _set(
        self,
        ctx: Context,
        *,
        timezone: zoneinfo.ZoneInfo = commands.param(converter=TimezoneConverter),
    ) -> None:
        """Set your timezone publicly in this guild. Please use formats like:-

        `America/New York`
        `Europe/London`
        `Asia/Tokyo`
        """
        if not ctx.guild:
            await ctx.send("Sorry, this command only works in DMs!", ephemeral=True)
            return

        query = """ INSERT INTO tz_store(user_id, tz)
                    VALUES ($1, $2)
                    ON CONFLICT (user_id) DO UPDATE
                    SET tz = $2
                    WHERE tz_store.user_id = $1;
                """
        async with ctx.typing(ephemeral=True):
            confirm = await ctx.prompt("This will make your timezone public in this guild, confirm?", delete_after=False)

            if not confirm:
                return

            await self.bot.pool.execute(query, ctx.author.id, timezone.key)
            if ctx.interaction:
                await ctx.interaction.edit_original_response(content="Done!")
                return

            await ctx.send("Done!", ephemeral=True)

    @timezone.command(name="remove")
    async def _remove(self, ctx: Context) -> None:
        """Remove your timezone from this guild."""
        if not ctx.guild:
            await ctx.send("Sorry, this command only works in DMs!", ephemeral=True)
            return

        query = """
            DELETE *
            FROM tz_store
            WHERE user_id = $1;
            """
        async with ctx.typing(ephemeral=True):
            await self.bot.pool.execute(query, ctx.author.id)

        await ctx.send("Done!", ephemeral=True)

    @timezone.command(name="info", aliases=["tz"])
    async def _info(
        self, ctx: Context, *, timezone: zoneinfo.ZoneInfo = commands.param(converter=TimezoneConverter)
    ) -> None:
        """This will return the time in a specified timezone."""
        embed = discord.Embed(
            title=f"Current time in {timezone}",
            description=f"```\n{self._curr_tz_time(timezone, ret_datetime=False)}\n```",
        )
        embed.set_footer(text=f"Requested by: {ctx.author}")
        embed.timestamp = datetime.datetime.now(datetime.timezone.utc)
        await ctx.send(embed=embed)

    @_info.autocomplete("timezone")
    @_set.autocomplete("timezone")
    async def timezone_autocomplete_callback(self, interaction: Interaction, current: str) -> list[app_commands.Choice[str]]:
        if not current:
            return self._timezone_cache[:25]

        options: set[str] = set(AVAILABLE_TIMEZONES)

        closest_matches = difflib.get_close_matches(word=current, possibilities=options, n=25, cutoff=0.6)

        starts_with = [zone for zone in options.difference(closest_matches) if zone.lower().startswith(current.lower())]

        cutoff = 25 - len(closest_matches)
        view_order = starts_with[:cutoff] + closest_matches

        return [app_commands.Choice(name=name, value=AVAILABLE_TIMEZONES[name]) for name in view_order]

    async def time_error(self, ctx: Context, error: commands.CommandError) -> None:
        """Quick error handling for timezones."""
        error = getattr(error, "original", error)
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("How am I supposed to do this if you don't supply the timezone?")

    async def _fetch_time_records(self, guild: discord.Guild, /) -> list[TimezoneRecord]:
        query = """
        SELECT user_id, tz
        FROM tz_store
        WHERE $1 = ANY(guild_ids);
        """

        return await self.bot.pool.fetch(query, guild.id)

    def _transform_records(
        self, records: list[TimezoneRecord], *, guild: discord.Guild
    ) -> list[tuple[str, str, datetime.timedelta]]:
        ret: list[tuple[str, str, datetime.timedelta]] = []
        for record in records:
            user_id = record["user_id"]
            user_ = guild.get_member(user_id)
            if user_:
                user = f"{user_.name}"
            else:
                user = f"Member with the ID {user_id} cannot be found."

            tz = record["tz"]
            timezone = zoneinfo.ZoneInfo(tz)
            offset = timezone.utcoffset(datetime.datetime.now(datetime.timezone.utc)) or datetime.timedelta(0)
            dt = datetime.datetime.now(timezone)
            ordinal_ = time.ordinal(dt.day)
            fmt = dt.strftime(f"%A {ordinal_} of %B %Y at %H:%M")

            ret.append((user, fmt, offset))

        ret.sort(key=lambda t: t[2])

        return ret

    @app_commands.command(name="time-board")
    async def time_board(self, interaction: Interaction) -> None:
        """This command will show a board of all public timezones in Dannyware."""
        if not interaction.guild:
            return await interaction.response.send_message(
                "Sorry, this command can only be used in a guild!", ephemeral=True
            )

        await interaction.response.defer(ephemeral=True)

        context = await Context.from_interaction(interaction)
        records: list[TimezoneRecord] = await self._fetch_time_records(interaction.guild)
        if not records:
            return await interaction.followup.send("Sorry but there are no recorded timezones here!", ephemeral=True)
        transformed = self._transform_records(records, guild=interaction.guild)

        source = TimezoneSource(data=transformed, per_page=10)
        pages = RoboPages(source=source, ctx=context, check_embeds=True, compact=False)

        await pages.start(content=f"This is the current timezone list for {interaction.guild.name}!", ephemeral=True)


async def setup(bot: Mipha) -> None:
    """Cog entrypoint."""
    await bot.add_cog(Time(bot))
