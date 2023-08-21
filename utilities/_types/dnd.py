from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from typing_extensions import NotRequired


__all__ = ("DnDClassTopLevel",)


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
