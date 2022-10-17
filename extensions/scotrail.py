from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import bs4
import discord
import tabulate
from discord import app_commands
from discord.ext import commands

from utilities import formats, fuzzy


if TYPE_CHECKING:
    from bot import Kukiko
    from utilities._types.scotrail import ScotrailData


class TrainTime:
    __slots__ = (
        "departs",
        "final_destination",
        "platform",
        "detail",
    )

    def __init__(self, *, departs: str, final_destination: str, platform: str, detail: str) -> None:
        self.departs: str = departs
        self.final_destination: str = final_destination
        self.platform: str = platform
        self.detail: str = detail

    def __repr__(self) -> str:
        return f"<TrainTime departs={self.departs!r} final_destination={self.final_destination!r} platform={self.platform!r} detail={self.detail!r}>"

    def __str__(self) -> str:
        delayed = self.detail != "On time"
        ret = f"Train departs at {self.departs}, with the final destination of {self.final_destination.title()}."

        if delayed:
            ret += f"\nHowever, it is currently delayed until {self.detail}."

        return ret

    def format(self) -> list[str]:
        return [self.departs, self.final_destination, self.platform, self.detail]


class Route:
    __slots__ = ("data",)

    def __init__(self, data: bs4.BeautifulSoup, /) -> None:
        self.data: bs4.BeautifulSoup = data

    def get_headers(self) -> list[str]:
        headers = self.data.find_all("th")
        if not headers:
            raise ValueError("Seems the HTML is malformed.")

        return [header.text for header in headers]

    def get_next_trains(self) -> list[TrainTime]:
        elements = self.data.find_all("tr", class_="service")
        ret: list[TrainTime] = []

        for element in elements:
            clean: list[str] = [child.text for child in element.children if isinstance(child, bs4.Tag)]
            ret.append(TrainTime(departs=clean[0], final_destination=clean[1], platform=clean[2], detail=clean[3]))

        return ret

    def table(self) -> str:
        headers = self.get_headers()
        trains = self.get_next_trains()

        table = tabulate.tabulate([train.format() for train in trains], headers=headers)

        return table


class ScotrailCog(commands.Cog):
    def __init__(self, bot: Kukiko, /) -> None:
        self.bot: Kukiko = bot
        self._station_path: Path = Path("configs/scotrail/stations.json")
        with self._station_path.open("r") as fp:
            self._station_data: ScotrailData = json.load(fp)

        self._choices: list[app_commands.Choice[str]] = []

    async def _get_stations(self) -> None:
        async with self.bot.session.get(
            "https://www.scotrail.co.uk/cache/trainline_stations/trainline?_=1530115581789"
        ) as resp:
            data: ScotrailData = await resp.json()

        if data["updated"] == self._station_data["updated"]:
            return

        with self._station_path.open("w") as fp:
            json.dump(data, fp)

    def _generate_autocomplete(self) -> list[app_commands.Choice[str]]:
        ret: list[app_commands.Choice[str]] = []

        for station_name, inner_data in self._station_data["stations"].items():
            ret.append(app_commands.Choice(name=station_name.title(), value=inner_data["crs"]))

        self._choices = ret

        return ret

    @app_commands.command(name="next-train", description="Check your next train times from Scotrail.")
    @app_commands.rename(from_="from")
    async def next_train_callback(self, interaction: discord.Interaction, from_: str, to: str) -> None:
        await interaction.response.defer(thinking=True)

        async with self.bot.session.get(f"https://scotrail.co.uk/cache/nre/next-trains/{from_}/{to}") as resp:
            data = await resp.text()

        if data == "No services between these stations have been found.":
            await interaction.followup.send("There are no services between these stations found.")
            return

        soup = bs4.BeautifulSoup(data, "lxml")
        route = Route(soup)

        table = route.table()

        codeblock = formats.to_codeblock(table, language="", escape_md=False)

        fmt = f"From: {from_}\nTo: {to}\n" + codeblock

        await interaction.followup.send(fmt)

    @next_train_callback.autocomplete("from_")
    @next_train_callback.autocomplete("to")
    async def next_train_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:

        if not current:
            quick = self._generate_autocomplete()
            return quick[:25]

        keys = [key.lower() for key in self._station_data["stations"].keys()]

        attempt = fuzzy.extract(current.lower(), keys, score_cutoff=20, limit=5)

        ret: list[app_commands.Choice[str]] = []
        for item, _ in attempt:
            ret.append(app_commands.Choice(name=item.title(), value=self._station_data["stations"][item.title()]["crs"]))

        return ret

    @next_train_callback.error
    async def error_handler(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        error = getattr(error, "original", error)

        respond = interaction.followup.send if interaction.response.is_done() else interaction.response.send_message

        if isinstance(error, ValueError):
            await respond(content="Sorry, I think you put in the wrong station details, or I just broke.")


async def setup(bot: Kukiko) -> None:
    await bot.add_cog(ScotrailCog(bot), guild=discord.Object(id=174702278673039360))
