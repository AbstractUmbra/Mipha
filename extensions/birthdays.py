from __future__ import annotations

import datetime
import logging
from typing import TYPE_CHECKING, Annotated

import discord
import zoneinfo
from discord import app_commands
from discord.ext import commands

from utilities.converters import DatetimeTransformer  # noqa: TCH001

if TYPE_CHECKING:
    from bot import Mipha
    from utilities.context import Interaction

    from .reminders import Reminder, Timer

LOGGER = logging.getLogger(__name__)


async def setup(bot: Mipha) -> None:
    await bot.add_cog(Birthdays(bot))


class Birthdays(commands.Cog):
    def __init__(self, bot: Mipha, /) -> None:
        self.bot: Mipha = bot

    birthday_group = app_commands.Group(
        name="birthday",
        description="A set of commands to assist you in remembering birthdays!",
        guild_ids=[1045411522421198939, 705500489248145459, 174702278673039360],
    )

    @birthday_group.command(name="set")
    async def set_birthday_callback(
        self,
        interaction: Interaction,
        who: str,
        date: Annotated[datetime.datetime, DatetimeTransformer],
        timezone: str | None = None,
    ) -> None:
        reminder: Reminder | None = self.bot.get_cog("Reminder")  # type: ignore # downstream narrowing
        if not reminder:
            return await interaction.response.send_message("Sorry this functionality is currently unavailable.")

        await interaction.response.defer(ephemeral=True)

        if timezone:
            try:
                resolved_timezone = zoneinfo.ZoneInfo(timezone)
            except zoneinfo.ZoneInfoNotFoundError:
                resolved_timezone = datetime.timezone.utc
        else:
            resolved_timezone = datetime.timezone.utc

        parsed_when = date.astimezone(resolved_timezone)

        timer = await reminder.create_timer(
            parsed_when,
            "birthday",
            interaction.user.id,
            who=int(who),
            created=interaction.created_at,
        )

        human_time = discord.utils.format_dt(timer.expires, "F")
        await interaction.followup.send(f"Okay! Their birthday is now set for {human_time}. I'll remind you then!")

    async def increment_birthday(self, timer: Timer, /) -> None:
        reminder: Reminder = self.bot.get_cog("Reminder")  # type: ignore # downstream narrowing
        if not reminder:
            return

        new_when = datetime.datetime(
            year=timer.expires.year + 1,
            month=timer.expires.month,
            day=timer.expires.day,
            hour=0,
            minute=0,
            second=0,
            tzinfo=timer.expires.tzinfo,
        )

        await reminder.create_timer(new_when, "birthday", *timer.args, **timer.kwargs)

    @set_birthday_callback.autocomplete("timezone")
    async def timezone_autocomplete(self, interaction: Interaction, argument: str) -> list[app_commands.Choice[str]]:
        reminders: Reminder = self.bot.get_cog("Reminder")  # type: ignore # downstream narrowing
        if not argument:
            return reminders._default_timezones

        matches = reminders.find_timezones(argument)
        return [tz.to_choice() for tz in matches[:25]]

    @commands.Cog.listener()
    async def on_birthday_timer_complete(self, timer: Timer) -> None:
        author_id = timer.args[0]

        try:
            author = self.bot.get_user(author_id) or await self.bot.fetch_user(author_id)
        except discord.HTTPException:
            return

        who = timer.kwargs["who"]
        try:
            who = self.bot.get_user(who) or await self.bot.fetch_user(who)
        except discord.HTTPException:
            return

        message = (
            f"Hey {author.mention}, you asked to be reminded of {who.mention} ({who.id})'s birthday. It is now their"
            " birthday!"
        )

        try:
            await author.send(message)
        except discord.HTTPException:
            return

        await self.increment_birthday(timer)
