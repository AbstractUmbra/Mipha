from __future__ import annotations

import datetime
import logging
import pathlib
from typing import TYPE_CHECKING

from discord.ext import commands, tasks
from discord.utils import _from_json

from utilities.cache import cache
from utilities.formats import to_codeblock

if TYPE_CHECKING:
    from discord.ext.commands._types import Check

    from bot import Mipha
    from utilities._types.config import CurrencyConfig
    from utilities._types.currencies import CurrencyData, CurrencyKey, CurrencyResponse, CurrencyStatus, CurrentExchangeRate
    from utilities.context import Context

LOGGER = logging.getLogger(__name__)
CURRENCY_DETAILS_PATH = pathlib.Path("configs/currencies.json")
with CURRENCY_DETAILS_PATH.open("r") as fp:
    CURRENCY_DETAILS: CurrencyResponse = _from_json(fp.read())


def _status_check() -> Check[Context[Currency]]:
    async def predicate(ctx: Context) -> bool:
        assert isinstance(ctx.cog, Currency)

        status = await ctx.cog._get_status()

        return status["quotas"]["month"]["remaining"] > 0

    return commands.check(predicate)


class CurrencyDetails:
    def __init__(self, base_currency: CurrencyKey, /, *, payload: CurrentExchangeRate) -> None:
        for key, value in payload["data"].items():
            # we just assign all currency keys as attrs with the value.
            setattr(self, key, value)

    def details(self, key: CurrencyKey) -> CurrencyData:
        return CURRENCY_DETAILS["data"][key]


class Currency(commands.Cog):
    def __init__(self, bot: Mipha, /, config_key: CurrencyConfig) -> None:
        self.bot: Mipha = bot
        self.api_key = config_key["api_key"]
        self.reset_cache.start()

    def __repr__(self) -> str:
        return "<Currency>"

    async def cog_unload(self) -> None:
        self.reset_cache.stop()

    @property
    def headers(self) -> dict[str, str]:
        return {"apikey": self.api_key}

    async def _get_status(self, base_currency: CurrencyKey = "GBP") -> CurrencyStatus:
        async with self.bot.session.get(
            "https://api.freecurrencyapi.com/v1/status", headers=self.headers, params={"base_currency": base_currency}
        ) as resp:
            data: CurrencyStatus = await resp.json()

        return data

    @cache()
    async def get_details(self, base: CurrencyKey) -> CurrencyDetails:
        async with self.bot.session.get("https://api.freecurrencyapi.com/v1/latest", headers=self.headers) as resp:
            data: CurrentExchangeRate = await resp.json()

        return CurrencyDetails(base, payload=data)

    @commands.group()
    async def currency(self, ctx: Context) -> None:
        if not ctx.invoked_subcommand:
            return await ctx.send_help(ctx.command)

    @currency.command()
    @commands.is_owner()
    async def status(self, ctx: Context) -> None:
        status = await self._get_status()

        fmt = to_codeblock(str(status["quotas"]["month"]), language="json", escape_md=False)

        await ctx.send(fmt)

    @currency.command()
    @_status_check()
    async def yen(self, ctx: Context, input_: float) -> None:
        ...

    @tasks.loop(time=datetime.time(hour=0, tzinfo=datetime.timezone.utc))
    async def reset_cache(self) -> None:
        keys = self.get_details.cache.keys()
        for k in keys:
            try:
                del self.get_details.cache[k]
            except KeyError:
                pass


async def setup(bot: Mipha, /) -> None:
    if key := bot.config.get("currency"):
        return await bot.add_cog(Currency(bot, config_key=key))
    LOGGER.warning("Not enabling the currency conversion cog due to no key existing.")
