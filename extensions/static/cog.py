from __future__ import annotations

import datetime
import itertools
import logging
import operator
import pathlib
import zoneinfo
from typing import TYPE_CHECKING, TypedDict
from urllib.parse import quote

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utilities.shared.converters import DatetimeTransformer  # ruff: ignore[typing-only-first-party-import] # this is used at runtime
from utilities.shared.formats import ts
from utilities.shared.time import Weekday, hf_time, resolve_next_weekday

if TYPE_CHECKING:
    from sqlite3 import Row

    import asqlite

    from bot import Mipha
    from utilities.context import Interaction

GUILD_ID: int = 1547968251076550776
AFK_CHANNEL_ID: int = 1550058389437026324
RAID_YAPPING_CHANNEL_ID: int = 1547969699772371025  # real
AFK_MESSAGE_ID: int = 1550088874410377332
MOUNT_FARM_ROLE_ID: int = 1551555982054658240
SAVAGE_ROLE_ID: int = 1547968460582166558
SUB_ROLE_ID: int = 1547968524755017768

DB_SCHEMA_FILE = pathlib.Path(__file__).parent / "schema.sql"

RAID_DAYS = (1, 3, 4)  # tues, thurs, fri
MOUNT_FARM_DAYS = (1,)  # tues
SAVAGE_DAYS = (3, 4)  # thurs, fri

ALEX_OFF_WEEK_START: datetime.datetime = datetime.datetime(2026, 9, 29, 20, tzinfo=zoneinfo.ZoneInfo("Europe/London"))

LOGGER = logging.getLogger(__name__)

AFK_PROSE = """
Reminder that the following folks are AFK today! ^^

{people_list}
"""

AFK_MESSAGE_PROSE = """
# Noted AFKs:

{people_list}
"""

SCHEDULE_PROSE = """
Hey folks!
Here is this weeks schedule:

{mount_farm_role_mention}
{mount_farm_event_url}

{savage_mention}
{savage_event_urls}

-# The Discord events have a URL for adding these to your Google Calendar if you like!
"""

__all__ = ("Static",)


class AFKMessageRecord(TypedDict):
    id: int
    who: int
    when: datetime.datetime


class Static(commands.Cog):
    def __init__(self, bot: Mipha, /, pool: asqlite.Pool) -> None:
        self.bot = bot
        self.pool = pool
        self.check_afks.start()
        self.post_raid_times.start()

    async def cog_load(self) -> None:
        schema_contents = DB_SCHEMA_FILE.read_text()
        async with self.pool.acquire() as conn:
            await conn.executescript(schema_contents)

    async def cog_unload(self) -> None:
        self.check_afks.cancel()
        self.post_raid_times.cancel()

    async def _insert_afk(self, *, when: datetime.datetime, who: discord.Member | discord.User) -> None:
        query = """
                INSERT INTO afks (who, afk_date)
                VALUES (?, ?);
                """

        when = when.replace(hour=20, minute=0, second=0, microsecond=0)

        async with self.pool.acquire() as conn:
            await conn.execute(query, who.id, round(when.timestamp()))

    async def _delete_afk(self, id_: int, /) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM afks WHERE id = (?);", id_)

    def _resolve_savage_days(self, *, source: datetime.datetime | None = None) -> tuple[int, int]:
        now = source or datetime.datetime.now(zoneinfo.ZoneInfo("Europe/London"))
        then = resolve_next_weekday(
            target=Weekday.tuesday,
            source=now,
            current_week_included=True,
            before_time=datetime.time(hour=20, tzinfo=datetime.UTC),
        )
        LOGGER.debug("[Static] -> [Alex D&D] :: Resolved this week to %s", then)
        if (then - ALEX_OFF_WEEK_START).days % 14 == 0:
            LOGGER.debug("[Static] -> [Alex D&D] :: Found this to be a present week.")
            return (3, 4)
        LOGGER.debug("[Static] -> [Alex D&D] :: Found this to be a non-present week.")
        return (1, 4)

    def _create_gcalendar_event(self, what: str, times: tuple[datetime.datetime, datetime.datetime]) -> str:
        # https://calendar.google.com/calendar/render?action=TEMPLATE&dates=20260926T130000Z%2F20260926T160000Z&location=%5BEU%5D%20Light%20DC&text=LFG%20Forked%20Tower%3A%20Blood%203rd%20Boss%20Prog%20Run%20-%20Yuma
        ret = "https://calendar.google.com/calendar/render?action=TEMPLATE"
        # dates
        start, end = times

        def gcal_safe(dt: datetime.datetime) -> str:
            return dt.strftime("%Y%m%d%H%M%S")

        ret += f"&dates={gcal_safe(start)}/{gcal_safe(end)}"
        ret += "&ctz=Europe/London"

        # details
        ret += f"&details={quote(what)}"

        # location
        ret += f"&location={quote('Discord VC | Lich @ Light')}"

        return f"[Add to Google Calendar]({ret})"

    async def create_next_events(
        self, savage_days: tuple[int, int], *, source: datetime.datetime | None = None, guild: discord.Guild | None = None
    ) -> list[discord.ScheduledEvent]:
        guild = guild or self.bot.get_guild(GUILD_ID)
        source = source or datetime.datetime.now(zoneinfo.ZoneInfo("Europe/London"))
        if not guild:
            raise commands.BadArgument("Unable to locate the guild to schedule dates.")

        mount_farm_day = tuple(set(RAID_DAYS).difference(savage_days))
        LOGGER.debug(
            "[Static] -> [Events Handling] :: Creating events for the week, mount farm day is %s", mount_farm_day[0]
        )

        events: list[discord.ScheduledEvent] = []

        for idx, day in enumerate(savage_days, start=1):
            # we assume `source` is the start of the week (Sunday).
            then = (source + datetime.timedelta(days=day)).replace(hour=20, minute=0)

            if day == 3:
                then += datetime.timedelta(minutes=30)
            events.append(
                await guild.create_scheduled_event(
                    name=f"Meat Cleavers Savage (Night {idx})",
                    start_time=then.astimezone(datetime.UTC),
                    end_time=then + datetime.timedelta(hours=2),
                    entity_type=discord.EntityType.external,
                    privacy_level=discord.PrivacyLevel.guild_only,
                    location="Discord is shitty, but in Raid VC | Lich @ Light",
                    description=self._create_gcalendar_event(
                        f"Meat Cleavers Savage (Night {idx})", times=(then, then + datetime.timedelta(hours=2))
                    ),
                )
            )
        mount_farm_then = (source + datetime.timedelta(days=mount_farm_day[0])).replace(
            hour=20, minute=30 if mount_farm_day == 3 else 0
        )
        events.append(
            await guild.create_scheduled_event(
                name="Meat Cleavers DT Mount Farming",
                start_time=mount_farm_then.astimezone(datetime.UTC),
                end_time=mount_farm_then + datetime.timedelta(hours=2),
                entity_type=discord.EntityType.external,
                privacy_level=discord.PrivacyLevel.guild_only,
                location="Discord is shitty, but in Raid VC | Lich @ Light",
                description=self._create_gcalendar_event(
                    "Meat Cleavers Mount Farming!", times=(mount_farm_then, mount_farm_then + datetime.timedelta(hours=2))
                ),
            )
        )

        return events

    def _verify_afk(self, who: discord.Member, when: datetime.datetime) -> bool:
        if who.get_role(SAVAGE_ROLE_ID) and when.weekday() in (self._resolve_savage_days(source=when)):
            return True
        if who.get_role(MOUNT_FARM_ROLE_ID) and when.weekday() in (MOUNT_FARM_DAYS):
            return True
        return bool(who.get_role(SUB_ROLE_ID) and when.weekday() in RAID_DAYS)

    async def fetch_afk_records(self) -> list[Row]:
        query = """
                SELECT *
                FROM afks;
                """
        async with self.pool.acquire() as conn:
            return await conn.fetchall(query)

    async def cleanup_afk_table(self) -> None:
        LOGGER.info("[Static] :: Cleanup of expired records starting.")
        rows = await self.fetch_afk_records()

        tz = zoneinfo.ZoneInfo("Europe/London")

        to_delete: list[int] = []

        for row in rows:
            then: int = row["afk_date"]
            then_dt = datetime.datetime.fromtimestamp(then, tz=tz)

            now = datetime.datetime.now(tz).replace(hour=20, minute=0, second=0, microsecond=0)
            if then_dt < now:
                to_delete.append(row["id"])

        if not to_delete:
            return

        async with self.pool.acquire() as conn:
            await conn.executemany("DELETE FROM afks WHERE id IN (?);", ", ".join(map(str, to_delete)))
        LOGGER.info("[Static] :: Cleanup of expired records finished.")

    async def update_afk_message(self) -> None:
        tz = zoneinfo.ZoneInfo("Europe/London")

        def transform(rows: list[Row]) -> list[AFKMessageRecord]:
            new_rows: list[AFKMessageRecord] = [
                {
                    "id": row["id"],
                    "who": row["who"],
                    "when": datetime.datetime.fromtimestamp(row["afk_date"], tz=tz),
                }
                for row in rows
            ]
            return new_rows

        rows = await self.fetch_afk_records()
        transformed = transform(rows)
        transformed.sort(key=operator.itemgetter("when"))

        then = datetime.datetime.now(tz) + datetime.timedelta(days=31)
        filtered = list(filter(lambda r: r["when"] < then, transformed))

        grouped = itertools.groupby(filtered, key=operator.itemgetter("when"))

        inner_fmt = ""
        guild = self.bot.get_guild(GUILD_ID)
        if not guild:
            LOGGER.error("[Static] :: unable to get the guild for afk message updates. Problem?")
            return
        channel = guild.get_channel(AFK_CHANNEL_ID)
        if not channel or not isinstance(channel, discord.TextChannel):
            LOGGER.error("[Static] :: unable to get the channel for afk message updates. Problem?")
            return

        for date, group in grouped:
            inner_fmt += f"- {ts(date):D}\n"
            for record in group:
                member = guild.get_member(record["who"])
                if not member:
                    LOGGER.warning(
                        "[Static] :: Unable to get member with id %s for afk message updates. Problem?", record["who"]
                    )
                    continue
                inner_fmt += f"    - {member.mention}\n"

        if len(filtered) != len(transformed):
            inner_fmt += "-# The above shows the next ~31 days only."

        partial = channel.get_partial_message(AFK_MESSAGE_ID)
        await partial.edit(content=AFK_MESSAGE_PROSE.format(people_list=inner_fmt))

    afk = app_commands.Group(
        name="afk",
        description="For managing afk days for the static!",
        guild_ids=[GUILD_ID],
        guild_only=True,
        allowed_contexts=discord.app_commands.AppCommandContext(guild=True, dm_channel=False, private_channel=False),
        allowed_installs=discord.app_commands.AppInstallationType(guild=True, user=False),
    )

    @afk.command(name="add", description="Add an afk day!")
    async def create_afk(
        self,
        interaction: Interaction,
        when: app_commands.Transform[datetime.datetime, DatetimeTransformer],
        who: discord.Member | None = None,
    ) -> None:
        await interaction.response.defer(thinking=True, ephemeral=True)

        assert interaction.guild  # command is guild only

        resolved_who = who or interaction.user

        if not isinstance(resolved_who, discord.User):
            resolved_who = interaction.guild.get_member(resolved_who.id)
            if not resolved_who:
                raise commands.CheckFailure("User who submitted this command is not present in the Raid guild.")

        if not self._verify_afk(resolved_who, when):  # pyright: ignore[reportArgumentType] # guarded
            await interaction.edit_original_response(
                content="You don't need to record this one, it's not your day or not a raid day, but I appreciate you!"
            )
            return

        await self._insert_afk(when=when, who=resolved_who)

        await interaction.edit_original_response(content="AFK recorded!")
        LOGGER.info("[Static] :: %s created an afk for %s at %s.", interaction.user, resolved_who, when)
        await self.update_afk_message()

    @afk.command(name="delete", description="Remove a previously saved AFK day")
    @app_commands.describe(to_delete="Which entry to delete (please be careful!)")
    async def delete_afk(self, interaction: Interaction, to_delete: int) -> None:
        await interaction.response.defer(thinking=True, ephemeral=True)

        await self._delete_afk(to_delete)

        await interaction.edit_original_response(content="Deleted!")
        await self.update_afk_message()

    @delete_afk.autocomplete(name="to_delete")
    async def delete_afk_autocomplete(self, interaction: Interaction, _: str) -> list[app_commands.Choice[int]]:
        rows = await self.fetch_afk_records()

        tz = zoneinfo.ZoneInfo("Europe/London")

        ret = []

        guild = interaction.guild or self.bot.get_guild(GUILD_ID)
        if not guild:
            return []

        for row in rows:
            who_id: int = row["who"]
            who = guild.get_member(who_id)
            if not who:
                continue
            when = row["afk_date"]
            when_dt = datetime.datetime.fromtimestamp(when, tz=tz)

            who_name = f"{who.display_name} @ {hf_time(when_dt, with_time=False)}"

            ret.append(app_commands.Choice(name=who_name, value=row["id"]))

        return ret

    @tasks.loop(time=datetime.time(hour=0, tzinfo=zoneinfo.ZoneInfo("Europe/London")))
    async def post_raid_times(self) -> None:
        source = datetime.datetime.now(zoneinfo.ZoneInfo("Europe/London"))

        if source.weekday() != 6:
            return

        savage_days = self._resolve_savage_days(source=source)
        LOGGER.debug("[Static] -> [Event Loop] :: Savage days for wc %s are %s", source, savage_days)
        events = await self.create_next_events(savage_days=savage_days, source=source)
        LOGGER.debug("[Static] -> [Event Loop] :: Events list as repr: %s", "\n".join([repr(x) for x in events]))

        mount_farm = events.pop()
        LOGGER.debug("[Static] -> [Event Loop] :: Mount Farm popped as: %r", mount_farm)

        guild = self.bot.get_guild(GUILD_ID)
        if not guild:
            self.post_raid_times.cancel()
            raise RuntimeError("Unable to get the raid guild. Cancelling.")
        raid_channel = guild.get_channel(RAID_YAPPING_CHANNEL_ID)
        assert isinstance(raid_channel, discord.TextChannel)

        mount_farm_role = guild.get_role(MOUNT_FARM_ROLE_ID)
        savage_role = guild.get_role(SAVAGE_ROLE_ID)

        if not mount_farm_role:
            self.post_raid_times.cancel()
            raise RuntimeError("Unable to get the mount farm role.")
        if not savage_role:
            self.post_raid_times.cancel()
            raise RuntimeError("Unable to get the savage role.")

        formatted = SCHEDULE_PROSE.format(
            mount_farm_role_mention=mount_farm_role.mention,
            mount_farm_event_url=mount_farm.url,
            savage_mention=savage_role.mention,
            savage_event_urls="\n".join([event.url for event in events]),
        )

        await raid_channel.send(formatted, allowed_mentions=discord.AllowedMentions.none())

    @tasks.loop(time=datetime.time(hour=12, tzinfo=zoneinfo.ZoneInfo("Europe/London")))
    async def check_afks(self) -> None:
        tz = zoneinfo.ZoneInfo("Europe/London")
        rows = await self.fetch_afk_records()

        today = datetime.datetime.now(tz).date()

        afks = []

        for row in rows:
            when: int = row["afk_date"]
            when_date = datetime.datetime.fromtimestamp(when, tz=tz).date()

            if when_date == today and (when_date.weekday() in RAID_DAYS):
                afks.append(row["who"])

        if not afks:
            return

        guild = self.bot.get_guild(GUILD_ID)
        if not guild:
            LOGGER.error("[Static] :: Unable to get static guild. Problem?")
            return

        bullets = ""
        for id_ in afks:
            member = guild.get_member(id_)
            if not member:
                LOGGER.error("[Static] :: Unable to get member with id %s. Problem?", id_)
                continue
            bullets += f"- {member.mention}\n"

        if not bullets:
            return

        channel = guild.get_channel(AFK_CHANNEL_ID)
        assert isinstance(channel, discord.TextChannel)

        await channel.send(AFK_PROSE.format(people_list=bullets), allowed_mentions=discord.AllowedMentions.none())

        await self.cleanup_afk_table()
        await self.update_afk_message()

    @check_afks.before_loop
    @post_raid_times.before_loop
    async def before_loops(self) -> None:
        await self.bot.wait_until_ready()
