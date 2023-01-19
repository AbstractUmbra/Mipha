"""
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at http://mozilla.org/MPL/2.0/.
"""

from __future__ import annotations

import asyncio
import datetime
import difflib
import zoneinfo
from typing import TYPE_CHECKING, Literal, overload

import discord
from discord import app_commands
from discord.ext import commands

from utilities import time
from utilities.context import Context, Interaction
from utilities.fuzzy import extract


if TYPE_CHECKING:
    from bot import Mipha

AVAILABLE_TIMEZONES = {zone.replace("_", " "): zone for zone in zoneinfo.available_timezones()}


class TimezoneConverter(commands.Converter[zoneinfo.ZoneInfo]):
    async def convert(self, ctx: Context, argument: str) -> zoneinfo.ZoneInfo:
        query = extract(query=argument.lower(), choices=zoneinfo.available_timezones(), limit=5)
        if argument.lower() not in {timezone.lower() for timezone in zoneinfo.available_timezones()}:
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


class Time(commands.Cog):
    """Time cog for fun time stuff."""

    def __init__(self, bot: Mipha) -> None:
        self.bot: Mipha = bot
        self._timezone_cache: list[app_commands.Choice[str]] = [
            app_commands.Choice(name=name, value=val) for name, val in AVAILABLE_TIMEZONES.items()
        ]

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        query = """
        WITH corrected AS (
            SELECT user_id, array_agg(guild_id) new_guild_ids
            FROM tz_store, unnest(guild_ids) WITH ORDINALITY guild_id
            WHERE guild_id != $1
            GROUP BY user_id
        )
        UPDATE tz_store
        SET guild_ids = new_guild_ids
        FROM corrected
        WHERE guild_ids <> new_guild_ids
        AND tz_store.user_id = corrected.user_id;
        """
        await self.bot.pool.execute(query, guild.id)

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

        if not ctx.guild:
            await ctx.send("Sorry, this command doesn't work in DMs!", ephemeral=True)
            return

        full_member = member or ctx.author

        async with ctx.typing(ephemeral=False):
            query = """SELECT *
                    FROM tz_store
                    WHERE user_id = $1
                    AND $2 = ANY(guild_ids);
                    """
            result = await self.bot.pool.fetchrow(query, full_member.id, ctx.guild.id)

            if not result:
                await ctx.send(f"No timezone for {full_member} set or it's not public in this guild.")
                return

            member_timezone = result["tz"]
            tz = await TimezoneConverter().convert(ctx, member_timezone)
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
        """Set your timezone publicly in this guild."""
        if not ctx.guild:
            await ctx.send("Sorry, this command only works in DMs!", ephemeral=True)
            return

        query = """ INSERT INTO tz_store(user_id, guild_ids, tz)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (user_id) DO UPDATE
                    SET guild_ids = tz_store.guild_ids || $2, tz = $3
                    WHERE tz_store.user_id = $1;
                """
        async with ctx.typing(ephemeral=True):
            confirm = await ctx.prompt("This will make your timezone public in this guild, confirm?", delete_after=False)

            if not confirm:
                return

            await self.bot.pool.execute(query, ctx.author.id, [ctx.guild.id], timezone.key)
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
            WITH corrected AS (
                SELECT user_id, array_agg(guild_id) new_guild_ids
                FROM tz_store, unnest(guild_ids) WITH ORDINALITY guild_id
                WHERE guild_id != $2
                AND user_id = $1
                GROUP BY user_id
            )
            UPDATE tz_store
            SET guild_ids = new_guild_ids
            FROM corrected
            WHERE guild_ids <> new_guild_ids
            AND tz_store.user_id = corrected.user_id;
            """
        async with ctx.typing(ephemeral=True):
            await self.bot.pool.execute(query, ctx.author.id, ctx.guild.id)

        await ctx.send("Done!", ephemeral=True)

    @timezone.command(name="clear")
    async def _clear(self, ctx: Context) -> None:
        """Clears your timezones from all guilds."""
        query = "DELETE FROM tz_store WHERE user_id = $1;"
        confirm = await ctx.prompt("Are you sure you wish to purge your timezone from all guilds?")
        if not confirm:
            return

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


async def setup(bot: Mipha) -> None:
    """Cog entrypoint."""
    await bot.add_cog(Time(bot))
