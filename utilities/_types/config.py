from __future__ import annotations

from typing import NotRequired, Required, TypedDict

__all__ = ("RootConfig",)


class BotConfig(TypedDict):
    token: str


class DatabaseConfig(TypedDict):
    dsn: str
    audio_dsn: NotRequired[str]
    host: NotRequired[str]
    user: NotRequired[str]
    password: NotRequired[str]
    database: NotRequired[str]
    port: NotRequired[int]


class WebhookConfig(TypedDict):
    logging: str
    mangadex: NotRequired[str]


class TokenConfig(TypedDict):
    mystbin: str
    github: str
    wanikani: str
    sonarr: str
    tiktok: str


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
    logging_webhooks: dict[str, list[str]]  # guild_id: [channels]
