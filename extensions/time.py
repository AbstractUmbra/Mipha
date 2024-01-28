"""
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at http://mozilla.org/MPL/2.0/.
"""

from __future__ import annotations

import collections
import datetime
import zoneinfo
from typing import TYPE_CHECKING, Literal, NamedTuple, TypedDict, overload

import discord
from discord import app_commands
from discord.ext import commands
from lxml import etree

from utilities.context import Context, GuildContext, Interaction
from utilities.shared import time
from utilities.shared.cache import cache
from utilities.shared.formats import plural, random_pastel_colour
from utilities.shared.fuzzy import finder
from utilities.shared.paginator import RoboPages, SimpleListSource

if TYPE_CHECKING:
    from bot import Mipha


class TimezoneRecord(TypedDict):
    user_id: int
    tz: str


class TimeZone(NamedTuple):
    label: str
    key: str

    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> TimeZone:
        assert isinstance(ctx.cog, Time)

        # Prioritise aliases because they handle short codes slightly better
        if argument in ctx.cog._timezone_aliases:
            return cls(key=argument, label=ctx.cog._timezone_aliases[argument])

        if argument in ctx.cog.valid_timezones:
            return cls(key=argument, label=argument)

        timezones = ctx.cog.find_timezones(argument)

        try:
            return await ctx.disambiguate(timezones, lambda t: t[0], ephemeral=True)
        except ValueError:
            raise commands.BadArgument(f"Could not find timezone for {argument!r}")

    def to_choice(self) -> app_commands.Choice[str]:
        return app_commands.Choice(name=self.label, value=self.key)

    def to_zone(self) -> zoneinfo.ZoneInfo:
        return zoneinfo.ZoneInfo(self.key)


class CLDRDataEntry(NamedTuple):
    description: str
    aliases: list[str]
    deprecated: bool
    preferred: str | None


class TimezoneSource(SimpleListSource[tuple[str, datetime.timedelta]]):
    def format_page(self, _: RoboPages, entries: list[tuple[str, datetime.timedelta]]) -> discord.Embed:
        embed = discord.Embed(title="Dannyware Timezones!", colour=random_pastel_colour())
        tz_dict: collections.defaultdict[int, list[str]] = collections.defaultdict(list)

        def to_hour(td: datetime.timedelta) -> int:
            seconds = round(td.total_seconds())
            return seconds // (60 * 60)

        for member_str, offset in entries:
            if not offset:
                offset = datetime.timedelta(0)

            houred_offset = to_hour(offset)
            tz_dict[houred_offset].append(member_str)

        now = discord.utils.utcnow()
        for key, value in sorted(tz_dict.items()):
            fmt = ""
            for idx, member in enumerate(value, start=1):
                fmt += f"{idx}. {member}\n"
            name = "UTC" if key == 0 else f"{plural(abs(key)):hour} {'ahead of' if key > 0 else 'behind'} UTC"
            dt = now + datetime.timedelta(hours=key)
            ordinal_ = time.ordinal(dt.day)
            time_fmt = dt.strftime(f"%A {ordinal_} of %B %Y at %H:%M")
            embed.add_field(
                name=f"{name} ({time_fmt})",
                value=fmt,
                inline=False,
            )

        embed.timestamp = discord.utils.utcnow()

        return embed


class Time(commands.Cog):
    """Cog for timezone related things."""

    DEFAULT_POPULAR_TIMEZONE_IDS = (
        # America
        "usnyc",  # America/New_York
        "uslax",  # America/Los_Angeles
        "uschi",  # America/Chicago
        "usden",  # America/Denver
        # India
        "inccu",  # Asia/Kolkata
        # Europe
        "trist",  # Europe/Istanbul
        "rumow",  # Europe/Moscow
        "gblon",  # Europe/London
        "frpar",  # Europe/Paris
        "esmad",  # Europe/Madrid
        "deber",  # Europe/Berlin
        "grath",  # Europe/Athens
        "uaiev",  # Europe/Kyev
        "itrom",  # Europe/Rome
        "nlams",  # Europe/Amsterdam
        "plwaw",  # Europe/Warsaw
        # Canada
        "cator",  # America/Toronto
        # Australia
        "aubne",  # Australia/Brisbane
        "ausyd",  # Australia/Sydney
        # Brazil
        "brsao",  # America/Sao_Paulo
        # Japan
        "jptyo",  # Asia/Tokyo
        # China
        "cnsha",  # Asia/Shanghai
    )

    def __init__(self, bot: Mipha) -> None:
        self.bot: Mipha = bot
        self.valid_timezones: set[str] = zoneinfo.available_timezones()
        self._timezone_aliases: dict[str, str] = {
            "Eastern Time": "America/New_York",
            "Central Time": "America/Chicago",
            "Mountain Time": "America/Denver",
            "Pacific Time": "America/Los_Angeles",
            # (Unfortunately) special case American timezone abbreviations
            "EST": "America/New_York",
            "CST": "America/Chicago",
            "MST": "America/Denver",
            "PST": "America/Los_Angeles",
            "EDT": "America/New_York",
            "CDT": "America/Chicago",
            "MDT": "America/Denver",
            "PDT": "America/Los_Angeles",
        }
        self._default_timezones: list[app_commands.Choice[str]] = []

    async def cog_load(self) -> None:
        await self.parse_bcp47_timezones()

    async def cog_command_error(self, ctx: Context, error: commands.CommandError) -> None:
        """Error handling for Time.py."""
        error = getattr(error, "original", error)
        if isinstance(error, commands.BadArgument):
            await ctx.send(str(error))

    async def parse_bcp47_timezones(self) -> None:
        async with self.bot.session.get(
            "https://raw.githubusercontent.com/unicode-org/cldr/main/common/bcp47/timezone.xml",
        ) as resp:
            if resp.status != 200:
                return

            parser = etree.XMLParser(ns_clean=True, recover=True, encoding="utf-8")
            tree = etree.fromstring(await resp.read(), parser=parser)

            # Build a temporary dictionary to resolve "preferred" mappings
            entries: dict[str, CLDRDataEntry] = {
                node.attrib["name"]: CLDRDataEntry(
                    description=node.attrib["description"],
                    aliases=node.get("alias", "Etc/Unknown").split(" "),
                    deprecated=node.get("deprecated", "false") == "true",
                    preferred=node.get("preferred"),
                )
                for node in tree.iter("type")
                # Filter the Etc/ entries (except UTC)
                if not node.attrib["name"].startswith(("utcw", "utce", "unk"))
                and not node.attrib["description"].startswith("POSIX")
            }

            for entry in entries.values():
                # These use the first entry in the alias list as the "canonical" name to use when mapping the
                # timezone to the IANA database.
                # The CLDR database is not particularly correct when it comes to these, but neither is the IANA database.
                # It turns out the notion of a "canonical" name is a bit of a mess. This works fine for users where
                # this is only used for display purposes, but it's not ideal.
                if entry.preferred is not None:
                    preferred = entries.get(entry.preferred)
                    if preferred is not None:
                        self._timezone_aliases[entry.description] = preferred.aliases[0]
                else:
                    self._timezone_aliases[entry.description] = entry.aliases[0]

            for key in self.DEFAULT_POPULAR_TIMEZONE_IDS:
                entry = entries.get(key)
                if entry is not None:
                    self._default_timezones.append(app_commands.Choice(name=entry.description, value=entry.aliases[0]))

    @cache()
    async def get_timezone(self, user_id: int, /) -> str | None:
        query = "SELECT tz from tz_store WHERE user_id = $1;"
        record = await self.bot.pool.fetchrow(query, user_id)
        return record["tz"] if record else None

    async def get_tzinfo(self, user_id: int, /) -> datetime.tzinfo:
        tz = await self.get_timezone(user_id)
        if tz is None:
            return datetime.UTC

        try:
            tz = zoneinfo.ZoneInfo(tz)
        except zoneinfo.ZoneInfoNotFoundError:
            tz = datetime.UTC

        return tz

    def find_timezones(self, query: str) -> list[TimeZone]:
        # A bit hacky, but if '/' is in the query then it's looking for a raw identifier
        # otherwise it's looking for a CLDR alias
        if "/" in query:
            return [TimeZone(key=a, label=a) for a in finder(query, self.valid_timezones)]

        keys = finder(query, self._timezone_aliases.keys())
        return [TimeZone(label=k, key=self._timezone_aliases[k]) for k in keys]

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
            tz = await TimeZone.convert(ctx, member_timezone)
            current_time = self._curr_tz_time(zoneinfo.ZoneInfo(tz.key), ret_datetime=False)
            embed = discord.Embed(title=f"Time for {full_member}", description=f"```\n{current_time}\n```")
            embed.set_footer(text=member_timezone)
            embed.timestamp = datetime.datetime.now(datetime.UTC)

        await ctx.send(embed=embed)

    @timezone.command(name="set")
    async def _set(
        self,
        ctx: Context,
        *,
        timezone: TimeZone,
    ) -> None:
        """Set your timezone publicly. Please use formats like:-

        `America/New York`
        `Europe/London`
        `Asia/Tokyo`
        """
        query = """ INSERT INTO tz_store(user_id, tz)
                    VALUES ($1, $2)
                    ON CONFLICT (user_id) DO UPDATE
                    SET tz = $2
                    WHERE tz_store.user_id = $1;
                """
        async with ctx.typing(ephemeral=True):
            confirm = await ctx.prompt(
                f"This will make {timezone.label!r} your public timezone public, confirm?",
                delete_after=False,
            )

            if not confirm:
                return

            await self.bot.pool.execute(query, ctx.author.id, timezone.key)
            if ctx.interaction:
                await ctx.interaction.edit_original_response(content="Done!")
                return

            await ctx.send("Done!", ephemeral=True)

    @timezone.command(name="remove")
    async def _remove(self, ctx: Context) -> None:
        """Remove your timezone from the bot."""
        query = """
            DELETE
            FROM tz_store
            WHERE user_id = $1;
            """
        async with ctx.typing(ephemeral=True):
            await self.bot.pool.execute(query, ctx.author.id)

        await ctx.send("Done!", ephemeral=True)

    @timezone.command(name="info", aliases=["tz"])
    async def _info(self, ctx: Context, *, timezone: TimeZone) -> None:
        """This will return the time in a specified timezone."""
        embed = discord.Embed(
            title=f"Current time in {timezone}",
            description=f"```\n{self._curr_tz_time(timezone.to_zone(), ret_datetime=False)}\n```",
        )
        embed.set_footer(text=f"Requested by: {ctx.author}")
        embed.timestamp = datetime.datetime.now(datetime.UTC)
        await ctx.send(embed=embed)

    @_info.autocomplete("timezone")
    @_set.autocomplete("timezone")
    async def timezone_autocomplete_callback(self, interaction: Interaction, current: str) -> list[app_commands.Choice[str]]:
        if not current:
            return self._default_timezones

        matches = self.find_timezones(current)
        return [tz.to_choice() for tz in matches[:25]]

    async def time_error(self, ctx: Context, error: commands.CommandError) -> None:
        """Quick error handling for timezones."""
        error = getattr(error, "original", error)
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("How am I supposed to do this if you don't supply the timezone?")

    def _transform_records(
        self,
        records: list[TimezoneRecord],
        *,
        guild: discord.Guild,
    ) -> list[tuple[str, datetime.timedelta]]:
        ret: list[tuple[str, datetime.timedelta]] = []
        for record in records:
            user_id = record["user_id"]
            user_ = guild.get_member(user_id)
            user = f"{user_.name}" if user_ else f"Member with the ID {user_id} cannot be found."

            tz = record["tz"]
            timezone = zoneinfo.ZoneInfo(tz)
            offset = timezone.utcoffset(datetime.datetime.now(datetime.UTC)) or datetime.timedelta(0)

            ret.append((user, offset))

        ret.sort(key=lambda t: t[1])

        return ret

    @app_commands.command(name="time-board")
    async def time_board(self, interaction: Interaction) -> None:
        """This command will show a board of all public timezones in Dannyware."""
        if not interaction.guild:
            return await interaction.response.send_message(
                "Sorry, this command can only be used in a guild!",
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True)

        context = await GuildContext.from_interaction(interaction)
        query = """
                SELECT user_id, tz
                FROM tz_store
                """
        records: list[TimezoneRecord] = await self.bot.pool.fetch(query)
        records = [*filter(lambda r: r["user_id"] in [m.id for m in context.guild.members], records)]
        if not records:
            return await interaction.followup.send("Sorry but there are no recorded timezones here!", ephemeral=True)
        transformed = self._transform_records(records, guild=interaction.guild)

        source = TimezoneSource(data=transformed, per_page=10)
        pages = RoboPages(source=source, ctx=context, check_embeds=True, compact=False)

        await pages.start(content=f"This is the current timezone list for {interaction.guild.name}!", ephemeral=True)


async def setup(bot: Mipha) -> None:
    """Cog entrypoint."""
    await bot.add_cog(Time(bot))
