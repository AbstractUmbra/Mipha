from __future__ import annotations

import datetime
import itertools
import logging
import operator
import pathlib
from typing import TYPE_CHECKING, TypedDict

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utilities.shared.converters import DatetimeTransformer  # ruff: ignore[typing-only-first-party-import] # this is used at runtime
from utilities.shared.formats import ts
from utilities.shared.time import hf_time

if TYPE_CHECKING:
    from sqlite3 import Row

    import asqlite

    from bot import Mipha
    from utilities.context import Interaction

GUILD_ID: int = 1547968251076550776
AFK_CHANNEL_ID: int = 1550058389437026324
AFK_MESSAGE_ID: int = 1550088874410377332
DB_SCHEMA_FILE = pathlib.Path(__file__).parent / "schema.sql"
RAID_DAYS = (1, 3, 4)  # tues, thurs, fri

LOGGER = logging.getLogger(__name__)

AFK_PROSE = """
Reminder that the following folks are AFK today! ^^

{people_list}
"""

AFK_MESSAGE_PROSE = """
# Noted AFKs:

{people_list}
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

    async def cog_load(self) -> None:
        schema_contents = DB_SCHEMA_FILE.read_text()
        async with self.pool.acquire() as conn:
            await conn.executescript(schema_contents)

    async def cog_unload(self) -> None:
        self.check_afks.cancel()

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

        to_delete: list[int] = []

        for row in rows:
            then: int = row["afk_date"]
            then_dt = datetime.datetime.fromtimestamp(then, tz=datetime.UTC)

            now = datetime.datetime.now(datetime.UTC).replace(hour=20, minute=0, second=0, microsecond=0)
            if then_dt < now:
                to_delete.append(row["id"])

        if not to_delete:
            return

        async with self.pool.acquire() as conn:
            await conn.executemany("DELETE FROM afks WHERE id IN (?);", ", ".join(map(str, to_delete)))
        LOGGER.info("[Static] :: Cleanup of expired records finished.")

    async def update_afk_message(self) -> None:
        def transform(rows: list[Row]) -> list[AFKMessageRecord]:
            new_rows: list[AFKMessageRecord] = [
                {
                    "id": row["id"],
                    "who": row["who"],
                    "when": datetime.datetime.fromtimestamp(row["afk_date"], tz=datetime.UTC),
                }
                for row in rows
            ]
            return new_rows

        rows = await self.fetch_afk_records()
        transformed = transform(rows)
        transformed.sort(key=operator.itemgetter("when"))

        grouped = itertools.groupby(transformed, key=operator.itemgetter("when"))

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

        resolved_who = who or interaction.user

        if when.weekday() not in RAID_DAYS:
            await interaction.edit_original_response(
                content="This isn't a recorded raid day for the cleavers, you're good! ^^"
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
            when_dt = datetime.datetime.fromtimestamp(when, tz=datetime.UTC)

            who_name = f"{who.display_name} @ {hf_time(when_dt, with_time=False)}"

            ret.append(app_commands.Choice(name=who_name, value=row["id"]))

        return ret

    @tasks.loop(time=datetime.time(hour=12, tzinfo=datetime.UTC))
    async def check_afks(self) -> None:
        rows = await self.fetch_afk_records()

        today = datetime.datetime.now(datetime.UTC).date()

        afks = []

        for row in rows:
            when: int = row["afk_date"]
            when_date = datetime.datetime.fromtimestamp(when, tz=datetime.UTC).date()

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
