from typing import Literal, TypeAlias, TypedDict

import discord


__all__ = (
    "MessageableGuildChannel",
    "KanjiDevKanjiPayload",
    "KanjiDevWordsPayload",
    "KanjiDevReadingPayload",
    "JishoWordsPayload",
    "JishoWordsResponse",
)

MessageableGuildChannel: TypeAlias = discord.TextChannel | discord.Thread | discord.VoiceChannel


class KanjiDevKanjiPayload(TypedDict):
    kanji: str
    grade: int | None
    stroke_count: int
    meanings: list[str]
    kun_readings: list[str]
    on_readings: list[str]
    name_readings: list[str]
    jlpt: int | None
    unicode: str
    heisig_en: str | None


class _KanjiDevMeanings(TypedDict):
    glosses: list[str]


class _KanjiDevVariants(TypedDict):
    written: str
    pronounced: str
    priorities: list[str]


class KanjiDevWordsPayload(TypedDict):
    meanings: list[_KanjiDevMeanings]
    variants: list[_KanjiDevVariants]


class KanjiDevReadingPayload(TypedDict):
    reading: str
    main_kanji: list[str]
    name_kanji: list[str]


class _JishoSenses(TypedDict):
    antonyms: list[str]
    english_definitions: list[str]
    info: list[str]
    links: list[dict[str, str]]
    parts_of_speech: list[str]
    restrictions: list[str]
    see_also: list[str]
    source: list[dict[str, str]]
    tags: list[str]


class _JishoJapanesePayload(TypedDict):
    word: str
    reading: str


class _JishoAttributions(TypedDict):
    jmdict: bool
    jmnedict: bool
    dbpedia: str | None


class JishoWordsPayload(TypedDict):
    slug: str
    is_common: bool
    tags: list[str]
    jlpt: list[str]
    japanese: list[_JishoJapanesePayload]
    senses: list[_JishoSenses]
    attribution: _JishoAttributions


class JishoWordsResponse(TypedDict):
    meta: dict[Literal["status"], Literal[200, 404]]
    data: list[JishoWordsPayload]
