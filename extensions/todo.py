"""
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at http://mozilla.org/MPL/2.0/.
"""

from __future__ import annotations

import datetime
import logging
from typing import TYPE_CHECKING, TypedDict

import discord
from discord import app_commands
from discord.ext import commands

from utilities.context import Interaction
from utilities.converters import DatetimeTransformer
from utilities.formats import random_pastel_colour
from utilities.time import human_timedelta
from utilities.ui import MiphaBaseModal, MiphaBaseView


if TYPE_CHECKING:
    from typing_extensions import Self

    from bot import Mipha
    from extensions.reminders import Reminder, Timer

LOGGER = logging.getLogger(__name__)


class TodoRecord(TypedDict):
    todo_id: int
    user_id: int
    channel_id: int
    todo_content: str
    todo_reminder: datetime.datetime | None
    todo_created_at: datetime.datetime


class TodoRescheduleModal(MiphaBaseModal, title="To-do rescheduling!"):
    when = discord.ui.TextInput(
        label="When to reschedule for?", style=discord.TextStyle.short, placeholder="Tomorrow at 3pm!"
    )

    async def on_submit(self, interaction: Interaction) -> None:
        await interaction.response.send_message("Okay, I have rescheduled your To-do!", ephemeral=True)
        self.stop()


class TodoCreateModal(MiphaBaseModal, title="To-do!"):
    what = discord.ui.TextInput(
        label="What have you got To-do?", style=discord.TextStyle.paragraph, placeholder="Buy some milk!"
    )
    when = discord.ui.TextInput(
        label="When do you wish to be reminded of this?",
        style=discord.TextStyle.short,
        placeholder="Tomorrow at 3pm!",
        required=False,
    )

    async def on_submit(self, interaction: Interaction) -> None:
        await interaction.response.send_message("Okay, I have created your To-do!", ephemeral=True)
        self.stop()


class TodoView(MiphaBaseView):
    message: discord.Message

    def __init__(self, *, bot: Mipha, record: TodoRecord, cog: Todo, timeout: float | None = 180):
        super().__init__(timeout=timeout)
        self.bot: Mipha = bot
        self.cog: Todo = cog
        self.__record: TodoRecord = record

    @discord.ui.button(label="Reschedule", emoji="\U000023f0")
    async def reschedule_button(self, interaction: Interaction, button: discord.ui.Button[Self]) -> None:
        modal = TodoRescheduleModal()
        await interaction.response.send_modal(modal)

        await modal.wait()

        dt = await DatetimeTransformer.transform(interaction, modal.when.value)

        await self.cog.create_todo(
            author_id=self.__record["user_id"],
            channel_id=self.__record["channel_id"],
            content=self.__record["todo_content"],
            reminder=dt,
        )


class Todo(commands.Cog):
    """
    A cog for 'todo' management and information.
    """

    def __init__(self, bot: Mipha) -> None:
        self.bot: Mipha = bot

    async def get_todos(self, interaction: Interaction) -> list[TodoRecord]:
        query = """
                SELECT *
                FROM todos
                WHERE user_id = $1
                ORDER BY todo_created_at ASC;
                """

        return await self.bot.pool.fetch(query, interaction.user.id)

    async def create_todo(
        self,
        *,
        author_id: int,
        channel_id: int,
        content: str,
        reminder: datetime.datetime | None,
    ) -> Timer | None:
        now = datetime.datetime.now(datetime.timezone.utc)
        query: str = """
                     INSERT INTO todos (user_id, channel_id, todo_content, todo_reminder, todo_created_at)
                     VALUES ($1, $2, $3, $4, $5)
                     RETURNING todo_id;
                     """

        record = await self.bot.pool.fetchrow(query, author_id, channel_id, content, reminder, now)

        if reminder is not None:
            reminder_cog: Reminder | None = self.bot.get_cog("Reminders")  # type: ignore # wtf dpy
            if reminder_cog is not None:
                return await reminder_cog.create_timer(
                    reminder, "todo_reminder", channel_id, created=now, todo_record=record[0]
                )

    async def fetch_todo(self, todo_id: int, /) -> TodoRecord | None:
        return await self.bot.pool.fetchrow("SELECT * FROM todos WHERE todo_id = $1;", todo_id)

    def generate_embed(self, record: TodoRecord, /) -> discord.Embed:
        ret = discord.Embed(title="To-do Reminder!", colour=random_pastel_colour())
        ret.description = "I was tasked to remind you of the following:-\n\n" + record["todo_content"]
        ret.add_field(name="Created:", value=human_timedelta(record["todo_created_at"]))
        ret.timestamp = record["todo_created_at"]

        author = self.bot.get_user(record["user_id"])
        if author is None:
            return ret

        ret.set_author(name=author.name, icon_url=author.display_avatar.url)
        return ret

    todo_group = app_commands.Group(name="todo", description="Commands to create and manage your To-do items!")

    @todo_group.command(name="create", description="Create a To-do for later.")
    async def todo_create(self, interaction: Interaction, what: str | None = None, when: str | None = None) -> None:
        assert interaction.channel

        content = what

        if what is None:
            modal = TodoCreateModal()
            await interaction.response.send_modal(modal)

            await modal.wait()

            content = modal.what.value

            if modal.when.value:
                parsed = await DatetimeTransformer.transform(interaction, modal.when.value)
            else:
                parsed = None
        else:
            content = what

            if when:
                parsed = await DatetimeTransformer.transform(interaction, when)
            else:
                parsed = None

        LOGGER.info("Got this: %s", what)

        await self.create_todo(
            author_id=interaction.user.id,
            channel_id=interaction.channel.id,
            content=content,
            reminder=parsed,
        )

        if what is not None:
            await interaction.response.send_message("Okay, I have created your to-do!", ephemeral=True)

    @todo_group.command(name="list", description="See a list of all your recorded to-dos!")
    async def todo_list(self, interaction: Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        records = await self.get_todos(interaction)

        if not records:
            return await interaction.followup.send("You don't seem to have any recorded things to-do.", ephemeral=True)

        embed = discord.Embed(title=f"{interaction.user}'s To-Dos!", colour=random_pastel_colour())
        description = ""
        for idx, record in enumerate(records, start=1):
            description += f"{idx}. {record['todo_content']}\n"

        embed.description = description

    @commands.Cog.listener()
    async def on_todo_reminder(self, timer: Timer, /) -> None:
        channel_id: int = timer.args[0]
        todo_id: int = timer.kwargs["todo_record"]

        todo_record = await self.fetch_todo(todo_id)
        if todo_record is None:
            return

        try:
            channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
        except discord.HTTPException:
            return

        assert isinstance(channel, discord.abc.MessageableChannel)

        view = TodoView(bot=self.bot, record=todo_record, cog=self)
        embed = self.generate_embed(todo_record)
        view.message = await channel.send(embed=embed, view=view)


async def setup(bot) -> None:
    await bot.add_cog(Todo(bot))
