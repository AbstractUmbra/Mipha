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

DANNYWARE_ID: int = 149998214810959872

DB_SCHEMA_FILE = pathlib.Path(__file__).parent / "schema.sql"


def pred(guild: discord.Guild, id_: int) -> bool:
    return id_ in range(1, 10) or bool(guild.get_member(id_))


class HeightTransformer(app_commands.Transformer):
    height_regex: re.Pattern[str] = re.compile(r"(?P<feet>\d(?:\'|ft)\d{1,2}\"?)|(?P<cm>\d{2,3}(?:\.\d)?(?:cm)?)")

    def feetinch_to_cm(self, value: str) -> float:
        value = value.replace("ft", "'")
        try:
            feet, inch = [*map(int, value.split("'"))]
        except (ValueError, TypeError) as err:
            msg_ = f"Unable to parse {value!r} as feet'inch."
            raise ValueError(msg_) from err

        feet_calc = feet * 30.48
        inch_calc = inch * 2.54

        return feet_calc * inch_calc

    async def transform(self, interaction: Interaction, value: str) -> float:
        match = self.height_regex.search(value)
        if not match:
            raise ValueError("Unable to parse input for height.")

        feet_group = match.group("feet")
        if feet_group:
            return self.feetinch_to_cm(feet_group)

        cm_group = match.group("cm")
        return float(cm_group.removesuffix("cm"))


@app_commands.guilds(discord.Object(id=DANNYWARE_ID), discord.Object(id=705500489248145459))
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

    async def fetch_all_records(self) -> list[Row]:
        async with self.pool.acquire() as conn:
            return await conn.fetchall("SELECT * FROM heights;")

    async def set_record(self, *, user_id: int, name: str, height: float) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute("INSERT OR REPLACE INTO heights VALUES (?, ?, ?);", user_id, name, height)

    @app_commands.command(name="image")
    @app_commands.describe(ephemeral="Whether to hide the output of the command, or not.", sort="The way to sort the image.")
    @app_commands.guild_only()
    async def get_height_image(
        self, interaction: Interaction, sort: SortKey = SortKey.height_desc, ephemeral: bool = False
    ) -> None:
        """Retrieve all stored heights based on current guild members."""
        assert interaction.guild

        await interaction.response.defer(ephemeral=ephemeral)
        await interaction.guild.chunk()

        rows = await self.fetch_all_records()
        transformed = {r["name"]: r["height"] for r in rows if pred(interaction.guild, r["user_id"])}

        buff = await asyncio.to_thread(make_figure, transformed, sort_key=sort)

        await interaction.followup.send(file=discord.File(buff, filename="heights.png"))

    @app_commands.command(name="set")
    @app_commands.describe(height="Your height in centimetres, or feet'inches")
    async def set_height(
        self,
        interaction: Interaction,
        height: app_commands.Transform[float, HeightTransformer],
        display_name: str | None = None,
    ) -> None:
        """Sets your height!"""
        if height >= 210:
            await interaction.response.send_message("I think you're lying.", ephemeral=True)
            return None

        view = ConfirmationView(timeout=15, author_id=interaction.user.id, delete_after=True)
        await interaction.response.send_message(
            content=f"Setting {height}cm as your height, confirm?", view=view, ephemeral=True
        )

        await view.wait()

        if view.value is True:
            await self.set_record(user_id=interaction.user.id, name=display_name or interaction.user.name, height=height)
            await interaction.edit_original_response(content="Set!")
            return None

        return await interaction.followup.send("Height not confirmed, aborting.", ephemeral=True)

    @app_commands.command(name="delete")
    async def delete_height(self, interaction: Interaction) -> None:
        """Remove any height data stored on you."""
        await interaction.response.defer()

        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM heights WHERE user_id = ?;", interaction.user.id)

        await interaction.followup.send("Gone.")
