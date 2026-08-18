from __future__ import annotations

import asyncio
import pathlib
import re
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from utilities.context import ConfirmationView

from .aaron import SortKey, make_figure

if TYPE_CHECKING:
    from sqlite3 import Row

    import asqlite

    from bot import Mipha
    from utilities.context import Interaction

DB_SCHEMA_FILE = pathlib.Path(__file__).parent / "schema.sql"


def get_scope(interaction: Interaction) -> int:
    if interaction.guild:
        return interaction.guild.id
    elif interaction.channel and interaction.channel.type == discord.ChannelType.group:
        return interaction.channel.id

    raise InvalidScope


class InvalidScope(app_commands.AppCommandError): ...


class HeightTransformer(app_commands.Transformer):
    height_regex: re.Pattern[str] = re.compile(
        r"(?P<feet>\d(?:\'|ft)\d{1,2}\"?)|(?P<cm>\d{2,3}(?:\.\d)?(?:cm)?)"
    )

    def feetinch_to_cm(self, value: str) -> float:
        value = value.replace("ft", "'")
        try:
            feet, inch = [*map(int, value.split("'"))]
        except (ValueError, TypeError) as err:
            msg_ = f"Unable to parse {value!r} as feet'inch."
            raise ValueError(msg_) from err

        feet_calc = feet * 30.48
        inch_calc = inch * 2.54

        return feet_calc + inch_calc

    async def transform(self, _: Interaction, value: str) -> float:
        match = self.height_regex.search(value)
        if not match:
            raise ValueError("Unable to parse input for height.")

        feet_group = match.group("feet")
        if feet_group:
            return self.feetinch_to_cm(feet_group)

        cm_group = match.group("cm")
        return float(cm_group.removesuffix("cm"))


class Heights(commands.GroupCog):
    def __init__(self, bot: Mipha, /, pool: asqlite.Pool) -> None:
        self.bot = bot
        self.pool = pool

    async def cog_load(self) -> None:
        schema_contents = DB_SCHEMA_FILE.read_text()
        async with self.pool.acquire() as conn:
            await conn.executescript(schema_contents)

    async def cog_unload(self) -> None:
        await self.pool.close()

    async def cog_app_command_error(
        self, interaction: Interaction, error: app_commands.AppCommandError
    ):
        strat = (
            interaction.response.send_message
            if not interaction.response.is_done()
            else interaction.followup.send
        )

        if isinstance(error, InvalidScope):
            return await strat(
                "This command can only be used in Servers or Group DMs.",
                ephemeral=True,
            )
        elif isinstance(error, app_commands.TransformerError):
            return await strat(
                "Unable to convert your input into a height.",
                ephemeral=True,
            )

        raise error

    async def fetch_scoped_records(self, scope_id: int) -> list[Row]:
        async with self.pool.acquire() as conn:
            return await conn.fetchall(
                "SELECT * FROM heights WHERE scope_id = ?;", scope_id
            )

    async def set_record(
        self,
        *,
        user_id: int,
        scope_id: int,
        name: str,
        height: float,
    ) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT OR REPLACE INTO heights VALUES (?, ?, ?, ?);",
                user_id,
                scope_id,
                name,
                height,
            )

    @app_commands.command(name="image")
    @app_commands.describe(
        ephemeral="Whether to hide the output of the command, or not.",
        sort="The way to sort the image.",
    )
    @app_commands.allowed_contexts(guilds=True, private_channels=True, dms=False)
    async def get_height_image(
        self,
        interaction: Interaction,
        sort: SortKey = SortKey.height_desc,
        ephemeral: bool = False,
    ) -> None:
        """Retrieve all stored heights based on current guild members."""

        scope_id = get_scope(interaction)

        await interaction.response.defer(ephemeral=ephemeral)

        rows = await self.fetch_scoped_records(scope_id)
        if not rows:
            return await interaction.followup.send("No heights recorded yet.")

        transformed = {r["name"]: r["height"] for r in rows}

        buff = await asyncio.to_thread(make_figure, transformed, sort_key=sort)

        await interaction.followup.send(file=discord.File(buff, filename="heights.png"))

    @app_commands.command(name="set")
    @app_commands.describe(height="Your height in centimetres, or feet'inches")
    @app_commands.allowed_contexts(guilds=True, private_channels=True, dms=False)
    async def set_height(
        self,
        interaction: Interaction,
        height: app_commands.Transform[float, HeightTransformer],
        display_name: app_commands.Range[str, 1, 20] | None = None,
    ) -> None:
        """Sets your height!"""
        if height >= 210 or height <= 60:
            await interaction.response.send_message(
                "I think you're lying.", ephemeral=True
            )

            return None

        scope_id = get_scope(interaction)

        view = ConfirmationView(
            timeout=15, author_id=interaction.user.id, delete_after=True
        )
        await interaction.response.send_message(
            content=f"Setting {height}cm as your height, confirm?",
            view=view,
            ephemeral=True,
        )

        await view.wait()

        if view.value is True:
            await self.set_record(
                user_id=interaction.user.id,
                scope_id=scope_id,
                name=display_name or interaction.user.name,
                height=height,
            )
            await interaction.edit_original_response(content="Set!")
            return None

        return await interaction.followup.send(
            "Height not confirmed, aborting.", ephemeral=True
        )

    @app_commands.command(name="delete")
    @app_commands.allowed_contexts(guilds=True, private_channels=True, dms=False)
    async def delete_height(self, interaction: Interaction) -> None:
        """Remove any height data stored on you."""

        scope_id = get_scope(interaction)

        await interaction.response.defer(ephemeral=True)

        async with self.pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM heights WHERE user_id = ? AND scope_id = ?;",
                interaction.user.id,
                scope_id,
            )

        await interaction.followup.send("Gone.", ephemeral=True)
