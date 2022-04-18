"""
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at http://mozilla.org/MPL/2.0/.
"""

from ._types import (
    JishoWordsPayload,
    KanjiDevKanjiPayload,
    KanjiDevWordsPayload,
    _JishoAttributions,
    _JishoJapanesePayload,
    _JishoSenses,
    _KanjiDevVariants,
)


__all__ = (
    "KanjiDevKanji",
    "KanjiDevWords",
    "JishoWord",
)


class KanjiDevKanji:
    __slots__ = ("_data",)

    def __init__(self, payload: KanjiDevKanjiPayload) -> None:
        self._data = payload

    @property
    def kanji(self) -> str:
        return self._data["kanji"]

    @property
    def grade(self) -> int | None:
        return self._data.get("grade", None)

    @property
    def stroke_count(self) -> int:
        return self._data["stroke_count"]

    @property
    def meanings(self) -> str:
        return "\n".join(self._data["meanings"])

    @property
    def kun_readings(self) -> str:
        return "\n".join(self._data["kun_readings"])

    @property
    def on_readings(self) -> str:
        return "\n".join(self._data["on_readings"])

    @property
    def name_readings(self) -> str:
        return "\n".join(self._data["name_readings"])

    @property
    def jlpt_level(self) -> int | None:
        return self._data.get("jlpt", None)

    @property
    def unicode(self) -> str:
        return self._data["unicode"]

    @property
    def heisig_en(self) -> str | None:
        return self._data.get("heisig_en", None)


class KanjiDevWords:
    __slots__ = ("_data",)

    def __init__(self, payload: KanjiDevWordsPayload) -> None:
        self._data = payload

    def meanings(self) -> str:
        fmt = []
        for meaning in self._data["meanings"]:
            fmt.extend(meaning["glosses"])

        return "\n".join(fmt)

    @property
    def variants(self) -> list[_KanjiDevVariants]:
        return self._data["variants"]

    def str_variants(self) -> str:
        fmt = []
        for variant in self._data["variants"]:
            fmt.append(f"{variant['written']} ({variant['pronounced']})")

        return "\n".join(fmt)


class JishoWord:
    def __init__(self, payload: JishoWordsPayload) -> None:
        self._data = payload
        self.slug: str = self._data["slug"]
        self.is_common: bool = self._data["is_common"]

    @property
    def tags(self) -> list[str]:
        return self._data["tags"]

    @property
    def jlpt(self) -> list[str]:
        return self._data["jlpt"]

    @property
    def words_and_readings(self) -> list[_JishoJapanesePayload]:
        return self._data["japanese"]

    @property
    def senses(self) -> list[_JishoSenses]:
        return self._data["senses"]

    @property
    def attributions(self) -> _JishoAttributions:
        return self._data["attribution"]
