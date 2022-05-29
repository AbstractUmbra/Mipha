from typing import Literal, TypeAlias, TypedDict

import discord
from typing_extensions import NotRequired


__all__ = (
    "MessageableGuildChannel",
    "KanjiDevKanjiPayload",
    "KanjiDevWordsPayload",
    "KanjiDevReadingPayload",
    "JishoWordsPayload",
    "JishoWordsResponse",
    "DnDClassTopLevel",
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


class DnDClassHD(TypedDict):
    number: int
    faces: int


DnDClassSkillsChoice = TypedDict(
    "DnDClassSkillsChoice",
    {
        "from": list[str],
        "count": int,
    },
)


class DnDClassStartingProficiencies(TypedDict):
    armor: list[str]
    weapons: list[str]
    tools: list[str]
    skiils: DnDClassSkillsChoice


class DnDClassStartingEquipmentBField(TypedDict):
    equipmentType: str
    quantity: int


class DnDClassStartingEquipmentDefaultData(TypedDict):
    a: NotRequired[list[str]]
    b: NotRequired[list[DnDClassStartingEquipmentBField]]
    _: NotRequired[list[str]]


class DnDClassStartingEquipment(TypedDict):
    additionalFromBackground: bool
    default: list[str]
    defaultData: DnDClassStartingEquipmentDefaultData


class DnDClassTableGroups(TypedDict):
    collabels: list[str]
    rows: list[list[int]]


class DnDClassClassFeatures(TypedDict):
    classFeature: str
    gainSubclassFeature: bool


class DnDClass(TypedDict):
    name: str
    source: str
    page: int
    isReprinted: bool
    hd: DnDClassHD
    proficiency: list[str]
    spellcastingAbility: str
    casterProgression: str
    spellsKnownProgression: list[int]
    startingProficiencies: DnDClassStartingProficiencies
    startingEquipment: DnDClassStartingEquipment
    classTableGroups: DnDClassTableGroups
    classFeatures: list[str | DnDClassClassFeatures]
    subclassTitle: str


class DnDSubClass(TypedDict):
    name: str
    shortName: str
    source: str
    className: str
    classSource: str
    page: int
    subclassFeatures: list[str]


class DnDClassFeatureOtherSource(TypedDict):
    source: str
    page: int


class DnDClassFeatureEntry(TypedDict):
    type: str
    items: list[str]


class DnDClassFeature(TypedDict):
    name: str
    source: str
    page: int
    otherSources: list[DnDClassFeatureOtherSource]
    className: str
    classSource: str
    level: int
    entries: list[str | DnDClassFeatureEntry]


class DnDSubClassFeatureEntry(TypedDict):
    type: str
    subclassFeature: str


class DnDSubClassFeature(TypedDict):
    name: str
    source: str
    page: int
    otherSources: DnDClassFeatureOtherSource
    className: str
    classSource: str
    subclassShortName: str
    subclassSource: str
    level: int
    entries: list[str | DnDSubClassFeatureEntry]


DnDClassTopLevel = TypedDict(
    "DnDClassTopLevel",
    {
        "class": list[DnDClass],
        "subclass": list[DnDSubClass],
        "classFeature": list[DnDClassFeature],
        "subclassFeature": list[DnDSubClassFeature],
    },
)
