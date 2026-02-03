from __future__ import annotations

import datetime
import logging
import pathlib
from enum import Enum
from typing import TYPE_CHECKING, Annotated, Literal, TypedDict

from discord import app_commands
from discord.ext import commands

from utilities.shared.async_config import Config
from utilities.shared.fuzzy import extract

if TYPE_CHECKING:
    from bot import Mipha
    from utilities.context import Context, Interaction

CONFIG_FILE_PATH = pathlib.Path("configs/currency.json")
API_KEY = "sgiPfh4j3aXFR3l2CnjWqdKQzxpqGn9pX5b3CUsz"

LOGGER = logging.getLogger(__name__)


CurrencyLiteral = Literal[
    "EUR",
    "USD",
    "JPY",
    "BGN",
    "CZK",
    "DKK",
    "GBP",
    "HUF",
    "PLN",
    "RON",
    "SEK",
    "CHF",
    "ISK",
    "NOK",
    "HRK",
    "RUB",
    "TRY",
    "AUD",
    "BRL",
    "CAD",
    "CNY",
    "HKD",
    "IDR",
    "ILS",
    "INR",
    "KRW",
    "MXN",
    "MYR",
    "NZD",
    "PHP",
    "SGD",
    "THB",
    "ZAR",
]


class CurrencyConfig(TypedDict):
    last_updated: str | None
    icon: str
    values: dict[CurrencyLiteral, float]


class Currency(Enum):
    EUR = "Euro"
    USD = "US Dollar"
    JPY = "Japanese Yen"
    BGN = "Bulgarian Lev"
    CZK = "Czech Republic Koruna"
    DKK = "Danish Krone"
    GBP = "British Pound Sterling"
    HUF = "Hungarian Forint"
    PLN = "Polish Zloty"
    RON = "Romanian Leu"
    SEK = "Swedish Krona"
    CHF = "Swiss Franc"
    ISK = "Icelandic Króna"
    NOK = "Norwegian Krone"
    HRK = "Croatian Kuna"
    RUB = "Russian Ruble"
    TRY = "Turkish Lira"
    AUD = "Australian Dollar"
    BRL = "Brazilian Real"
    CAD = "Canadian Dollar"
    CNY = "Chinese Yuan"
    HKD = "Hong Kong Dollar"
    IDR = "Indonesian Rupiah"
    ILS = "Israeli New Sheqel"
    INR = "Indian Rupee"
    KRW = "South Korean Won"
    MXN = "Mexican Peso"
    MYR = "Malaysian Ringgit"
    NZD = "New Zealand Dollar"
    PHP = "Philippine Peso"
    SGD = "Singapore Dollar"
    THB = "Thai Baht"
    ZAR = "South African Rand"


APP_COMMAND_CHOICES = [app_commands.Choice[str](name=item.value, value=item.name) for item in Currency]


class CurrencyCog(commands.Cog):
    def __init__(self, bot: Mipha, /, *, config: Config[dict[CurrencyLiteral, CurrencyConfig]]) -> None:
        self.bot: Mipha = bot
        self.config: Config[dict[CurrencyLiteral, CurrencyConfig]] = config

    def get_last_updated(self, key: CurrencyConfig) -> datetime.datetime:
        value = key["last_updated"]
        return (
            datetime.datetime.fromisoformat(value)
            if value
            else (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=30))
        )

    async def update_config(self, key: CurrencyLiteral) -> None:
        LOGGER.info("Updating currency config for key %r", key)
        async with self.bot.session.get(
            "https://api.freecurrencyapi.com/v1/latest", headers={"apikey": API_KEY}, params={"base_currency": key}
        ) as resp:
            resp.raise_for_status()
            data: dict[Literal["data"], dict[CurrencyLiteral, float]] = await resp.json()

            local_data = self.config["0"]
            now = datetime.datetime.now(datetime.UTC).isoformat()
            local_data[key].update({"last_updated": now, "values": data["data"]})

            await self.config.put("0", local_data)
        LOGGER.info("Currency config for %r now updated.", key)

    @commands.hybrid_command()
    @app_commands.rename(from_="from")
    @app_commands.describe(
        to="The target currency value we want to see",
        from_="The currency we're converting from.",
        amount="The amount of currency.",
    )
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    async def convert(
        self, ctx: Context, from_: Annotated[CurrencyLiteral, str], to: Annotated[CurrencyLiteral, str], amount: float
    ) -> None:
        """Convert your currency amounts!"""
        if ctx.interaction:
            await ctx.interaction.response.defer()

        from_config_key = self.config["0"][from_]
        to_config_key = self.config["0"][to]
        now = datetime.datetime.now(datetime.UTC)
        last_updated = self.get_last_updated(from_config_key)

        if (now - last_updated).days >= 1:
            LOGGER.info("Expired or missing information on %r, updating", from_)
            await self.update_config(to)
            from_config_key = self.config["0"][from_]
            to_config_key = self.config["0"][to]
        else:
            LOGGER.info("Cache hit for %r", from_)

        target = from_config_key["values"][to]
        calculated = round(amount * target, 2)

        from_name = Currency[from_].name
        from_icon = from_config_key["icon"]
        to_name = Currency[to].name
        to_icon = to_config_key["icon"]

        await ctx.send(f"{from_icon}{amount} `{from_name}` converted to `{to_name}` is {to_icon}{calculated}")

    @convert.autocomplete("from_")
    @convert.autocomplete("to")
    async def currency_autocomplete(self, _: Interaction, value: str) -> list[app_commands.Choice[str]]:
        if not value:
            return APP_COMMAND_CHOICES[:25]

        matches = [x[0] for x in extract(value, [choice.name for choice in APP_COMMAND_CHOICES], score_cutoff=50, limit=25)]
        return [x for x in APP_COMMAND_CHOICES if x.name in matches]


async def setup(bot: Mipha, /) -> None:
    if CONFIG_FILE_PATH.exists():
        return await bot.add_cog(CurrencyCog(bot, config=Config(CONFIG_FILE_PATH)))
    return None
