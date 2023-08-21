from typing import Literal, TypedDict

__all__ = (
    "CurrencyResponse",
    "CurrencyStatus",
    "CurrencyStatusDetails",
    "CurrentExchangeRate",
)

CurrencyKey = Literal[
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


class CurrencyData(TypedDict):
    symbol: str
    name: str
    symbol_native: str
    decimal_digits: int
    rounding: int
    code: str
    name_plural: str


class CurrencyResponse(TypedDict):
    data: dict[CurrencyKey, CurrencyData]


class CurrentExchangeRate(TypedDict):
    data: dict[CurrencyKey, float]


class CurrencyStatusDetails(TypedDict):
    total: int
    used: int
    remaining: int


class CurrencyGraceDetails(TypedDict):
    total: int
    used: int
    remaining: int


class _CurrencyStatusInner(TypedDict):
    month: CurrencyStatusDetails
    grace: CurrencyGraceDetails


class CurrencyStatus(TypedDict):
    account_id: int
    quotas: _CurrencyStatusInner
