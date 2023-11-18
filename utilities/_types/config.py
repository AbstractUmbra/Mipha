from __future__ import annotations

from typing import NotRequired, Required, TypedDict

__all__ = ("RootConfig",)


class BotConfig(TypedDict):
    token: str


class DatabaseConfig(TypedDict, total=False):
    dsn: Required[str]
    audio_dsn: str
    host: str
    user: str
    password: str
    database: str
    port: int


class WebhookConfig(TypedDict):
    logging: str
    mangadex: NotRequired[str]


class TokenConfig(TypedDict, total=False):
    mystbin: str
    github: str
    wanikani: str
    sonarr: str
    tiktok: str
    pythonista_api: str


class MangaDexConfig(TypedDict):
    username: str
    password: str


class UploaderConfig(TypedDict):
    token: str


class CurrencyConfig(TypedDict):
    api_key: str


class DeeplConfig(TypedDict):
    api_key: str


class RCONConfig(TypedDict):
    host: str
    password: str
    port: int


class DucklingConfig(TypedDict):
    host: str
    port: int


class _BooruConfig(TypedDict):
    api_key: str
    user_id: str


class LewdConfig(TypedDict):
    gelbooru: _BooruConfig
    danbooru: _BooruConfig


class Logging(TypedDict):
    dm: bool
    webhooks: list[str]


class RootConfig(TypedDict, total=False):
    bot: Required[BotConfig]
    owner_ids: Required[list[int]]
    intents: int
    postgresql: Required[DatabaseConfig]
    webhooks: Required[WebhookConfig]
    tokens: TokenConfig
    mangadex: MangaDexConfig
    uploader: UploaderConfig
    deepl: DeeplConfig
    currency: CurrencyConfig
    rcon: RCONConfig
    duckling: DucklingConfig
    lewd: LewdConfig
    logging_webhooks: dict[str, Logging]  # guild_id: [channels]
