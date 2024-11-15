from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from typing import NotRequired, Required

__all__ = ("RootConfig",)


class BotConfig(TypedDict):
    token: str
    dev_guilds: NotRequired[list[int]]
    owner_ids: list[int]
    intents: int


class DatabaseConfig(TypedDict):
    dsn: NotRequired[str]
    audio_dsn: NotRequired[str]
    host: str
    user: str
    password: str
    database: str
    port: int


class RTFSConfig(TypedDict):
    url: str
    token: str


class PyrightConfig(TypedDict):
    url: str
    token: str


class RedisConfig(TypedDict):
    url: str
    port: int
    password: str
    mock: bool


class WebhookConfig(TypedDict):
    logging: str
    mangadex: NotRequired[str]


class TokenConfig(TypedDict, total=False):
    github: str
    wanikani: str
    sonarr: str
    tiktok: str
    pythonista_api: str
    deepl: str


class MangaDexConfig(TypedDict):
    username: str
    password: str
    client_id: str
    client_secret: str


class UploaderConfig(TypedDict):
    token: str


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
    postgresql: Required[DatabaseConfig]
    rtfs: RTFSConfig
    pyright: PyrightConfig
    webhooks: Required[WebhookConfig]
    tokens: TokenConfig
    mangadex: MangaDexConfig
    uploader: UploaderConfig
    rcon: RCONConfig
    duckling: DucklingConfig
    lewd: LewdConfig
    logging_webhooks: dict[str, Logging]  # guild_id: [channels]
