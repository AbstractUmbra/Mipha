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
    code: str
    decimal_digits: int
    name: str
    name_plural: str
    rounding: int
    symbol: str
    symbol_native: str
    type: str
    last_updated: str | None  # custom
    values: dict[CurrencyLiteral, float]  # custom


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
        self.__token: str = self.config["token"]  # pyright: ignore[reportAttributeAccessIssue] # we lie

    async def first_setup(self) -> None:
        async with self.bot.session.get(
            "https://api.freecurrencyapi.com/v1/currencies", headers={"apiKey": self.__token}
        ) as resp:
            resp.raise_for_status()

            data = await resp.json()

            inner = self.config["0"]
            inner = data["data"]

            await self.config.put("0", inner)

    async def perform_updates(self, from_: CurrencyLiteral) -> None:
        now = datetime.datetime.now(datetime.UTC)

        config_entry = self.config["0"].get(from_)
        if not config_entry:
            LOGGER.warning("No entry for %r, forcing an update.", from_)
            await self.update_config(from_)
            return

        last_updated = self.get_last_updated(config_entry)

        if not ((now - last_updated).days >= 1):
            LOGGER.info("Cache hit for %r", from_)

        LOGGER.info("Expired or missing information on %r, updating", from_)
        await self.update_config(from_)

    def get_last_updated(self, key: CurrencyConfig) -> datetime.datetime:
        value = key.get("last_updated")
        return (
            datetime.datetime.fromisoformat(value)
            if value
            else (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=30))
        )

    async def update_config(self, from_: CurrencyLiteral) -> None:
        async with self.bot.session.get(
            "https://api.freecurrencyapi.com/v1/latest",
            headers={"apikey": self.__token},
            params={"base_currency": from_},
        ) as resp:
            resp.raise_for_status()
            data: dict[Literal["data"], dict[CurrencyLiteral, float]] = await resp.json()

        local_data = self.config["0"]
        now = datetime.datetime.now(datetime.UTC).isoformat()
        inner: CurrencyConfig = local_data.get(from_, {})  # pyright: ignore[reportAssignmentType]
        inner.update({"last_updated": now, "values": data["data"]})
        local_data[from_] = inner

        await self.config.put("0", local_data)
        LOGGER.info("Currency config for %r now updated.", from_)

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

        await self.perform_updates(from_)
        await self.perform_updates(to)

        from_config_key = self.config["0"][from_]
        to_config_key = self.config["0"][to]

        target = from_config_key["values"][to]
        calculated = round(amount * target, to_config_key["decimal_digits"])

        from_name = Currency[from_].name
        from_icon = from_config_key["symbol"]
        to_name = Currency[to].name
        to_icon = to_config_key["symbol"]

        await ctx.send(f"{from_icon}{amount} `{from_name}` converted to `{to_name}` is {to_icon}{calculated}")

    @convert.autocomplete("from_")
    @convert.autocomplete("to")
    async def currency_autocomplete(self, _: Interaction, value: str) -> list[app_commands.Choice[str]]:
        if not value:
            return APP_COMMAND_CHOICES[:25]

        matches = [x[0] for x in extract(value, [choice.name for choice in APP_COMMAND_CHOICES], score_cutoff=20, limit=25)]
        return [x for x in APP_COMMAND_CHOICES if x.name in matches]


async def setup(bot: Mipha, /) -> None:
    if CONFIG_FILE_PATH.exists():
        await bot.add_cog(CurrencyCog(bot, config=Config(CONFIG_FILE_PATH)))
