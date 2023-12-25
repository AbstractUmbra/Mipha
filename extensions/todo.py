"""
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at http://mozilla.org/MPL/2.0/.
"""

from __future__ import annotations

import datetime
import logging
import textwrap
from typing import TYPE_CHECKING, Annotated, TypedDict

import discord
from discord import app_commands
from discord.ext import commands

from utilities.shared.cache import cache
from utilities.shared.converters import DatetimeTransformer
from utilities.shared.formats import plural, random_pastel_colour
from utilities.shared.time import human_timedelta
from utilities.shared.ui import BaseModal, BaseView

if TYPE_CHECKING:
    from typing_extensions import Self

    from bot import Mipha
    from extensions.reminders import Reminder, Timer
    from utilities.context import Interaction

LOGGER = logging.getLogger(__name__)


class TodoRecord(TypedDict):
    todo_id: int
    user_id: int
    channel_id: int
    todo_content: str
    todo_reminder: datetime.datetime | None
    todo_created_at: datetime.datetime


class TodoRescheduleModal(BaseModal, title="To-do rescheduling!"):
    when = discord.ui.TextInput(
        label="When to reschedule for?",
        style=discord.TextStyle.short,
        placeholder="Tomorrow at 3pm!",
    )

    async def on_submit(self, interaction: Interaction) -> None:
        await interaction.response.send_message("Okay, I have rescheduled your To-do!", ephemeral=True)
        self.stop()


class TodoCreateModal(BaseModal, title="To-do!"):
    what = discord.ui.TextInput(
        label="What have you got To-do?",
        style=discord.TextStyle.paragraph,
        placeholder="Buy some milk!",
    )
    when = discord.ui.TextInput(
        label="When do you wish to be reminded of this?",
        style=discord.TextStyle.short,
        placeholder="Tomorrow at 3pm!",
        required=False,
    )

    def __init__(self, what_prefill: str | None = None) -> None:
        super().__init__()
        self.what.default = what_prefill

    async def on_submit(self, interaction: Interaction) -> None:
        await interaction.response.send_message("Okay, I have created your To-do!", ephemeral=True)
        self.stop()


class TodoView(BaseView):
    message: discord.Message

    def __init__(self, *, bot: Mipha, record: TodoRecord, cog: Todo, timeout: float | None = 180) -> None:
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
        self.create_todo_context_menu = app_commands.ContextMenu(
            name="Create To-do!",
            callback=self.create_todo_context_menu_callback,
            type=discord.AppCommandType.message,
        )
        self.bot.tree.add_command(self.create_todo_context_menu)

    def __repr__(self) -> str:
        return "<TodoCog>"

    async def cog_unload(self) -> None:
        self.bot.tree.remove_command(self.create_todo_context_menu.name, type=self.create_todo_context_menu.type)

    @cache()
    async def get_todos(self, user_id: int) -> list[TodoRecord]:
        query = """
                SELECT *
                FROM todos
                WHERE user_id = $1
                ORDER BY todo_created_at ASC;
                """

        return await self.bot.pool.fetch(query, user_id)

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

        assert record

        if reminder is not None:
            reminder_cog: Reminder | None = self.bot.get_cog("Reminder")  # type: ignore # wtf dpy
            if reminder_cog is not None:
                await reminder_cog.create_timer(reminder, "todo_reminder", channel_id, created=now, todo_record=record[0])
                self.get_todos.invalidate(self, author_id)
            else:
                LOGGER.warning("Reminder cog is not loaded. Is this an issue?")
                raise commands.CheckFailure("This functionality is currently unavailable.")

    async def fetch_todo(self, todo_id: int, /) -> TodoRecord | None:
        return await self.bot.pool.fetchrow("SELECT * FROM todos WHERE todo_id = $1;", todo_id)

    async def delete_todo(self, todo_id: int, /, *, author_id: int) -> bool:
        query = """
                DELETE
                FROM todos
                WHERE todo_id = $1
                AND author_id = $2;
                """

        execution = await self.bot.pool.execute(query, todo_id, author_id)

        if execution == "":
            return False
        return True

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

    async def create_todo_context_menu_callback(self, interaction: Interaction, message: discord.Message) -> None:
        create_modal = TodoCreateModal(what_prefill=message.content)
        await interaction.response.send_modal(create_modal)

    @todo_group.command(name="create", description="Create a To-do for later.")
    async def todo_create(
        self,
        interaction: Interaction,
        what: str | None = None,
        when: Annotated[datetime.datetime, DatetimeTransformer] | None = None,
    ) -> None:
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

            parsed = when

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

        if len(records) > 10:
            embed.set_footer(text="Only showing up to 10 to-do items.")
            records = records[:10]
        else:
            embed.set_footer(text=f"{plural(len(records)):record}")

        for record in records:
            shorten = textwrap.shorten(record["todo_content"], width=512)
            if record["todo_reminder"]:
                shorten += f": {discord.utils.format_dt(record['todo_reminder'], 'R')}"
            embed.add_field(name=str(record["todo_id"]) + ".", value=shorten, inline=False)

        await interaction.followup.send(embed=embed)

    @todo_group.command(name="delete", description="Delete one of your to-dos!")
    async def todo_delete(self, interaction: Interaction, todo: int) -> None:
        await interaction.response.defer(ephemeral=True)

        success = await self.delete_todo(todo, author_id=interaction.user.id)

        if success:
            self.get_todos.invalidate(self, interaction.user.id)
            await interaction.followup.send("Done!")
        else:
            await interaction.followup.send("Sorry, are you sure this is your todo?")

    @todo_delete.autocomplete(name="todo")
    async def todo_delete_autocomplete(self, interaction: Interaction, current: str) -> list[app_commands.Choice[int]]:
        todos = await self.get_todos(interaction.user.id)
        choices = [
            app_commands.Choice(
                name=textwrap.shorten(todo["todo_content"], width=20, placeholder="..."),
                value=todo["todo_id"],
            )
            for todo in todos
        ]

        return choices[:25]

    @commands.Cog.listener()
    async def on_todo_reminder_timer_complete(self, timer: Timer, /) -> None:
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
        view.message = await channel.send(
            content=f"<@{todo_record['user_id']}>",
            embed=embed,
            view=view,
            allowed_mentions=discord.AllowedMentions(users=True),
        )


async def setup(bot: Mipha) -> None:
    await bot.add_cog(Todo(bot))
