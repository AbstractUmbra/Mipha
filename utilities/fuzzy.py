"""
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at http://mozilla.org/MPL/2.0/.
"""

import heapq
import re
from collections.abc import Iterable
from difflib import SequenceMatcher
from typing import Any, Callable, Generator, Literal, Sequence, TypeVar, overload


T = TypeVar("T")


def ratio(a: Sequence[Any], b: Sequence[Any]) -> int:
    m = SequenceMatcher(None, a, b)
    return int(round(100 * m.ratio()))


def quick_ratio(a: Sequence[Any], b: Sequence[Any]) -> int:
    m = SequenceMatcher(None, a, b)
    return int(round(100 * m.quick_ratio()))


def partial_ratio(a: Sequence[Any], b: Sequence[Any]) -> int:
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    m = SequenceMatcher(None, short, long)

    blocks = m.get_matching_blocks()

    scores = []
    for i, j, _ in blocks:
        start = max(j - i, 0)
        end = start + len(short)
        o = SequenceMatcher(None, short, long[start:end])
        r = o.ratio()

        if 100 * r > 99:
            return 100
        scores.append(r)

    return int(round(100 * max(scores)))


_word_regex = re.compile(r"\W", re.IGNORECASE)


def _sort_tokens(a: str) -> str:
    a = _word_regex.sub(" ", a).lower().strip()
    return " ".join(sorted(a.split()))


def token_sort_ratio(a: str, b: str) -> int:
    a = _sort_tokens(a)
    b = _sort_tokens(b)
    return ratio(a, b)


def quick_token_sort_ratio(a: str, b: str) -> int:
    a = _sort_tokens(a)
    b = _sort_tokens(b)
    return quick_ratio(a, b)


def partial_token_sort_ratio(a: str, b: str) -> int:
    a = _sort_tokens(a)
    b = _sort_tokens(b)
    return partial_ratio(a, b)


@overload
def _extraction_generator(
    query: Sequence[Any],
    choices: Sequence[T],
    scorer: Callable[[Sequence[Any], Sequence[Any]], int] = quick_ratio,
    score_cutoff: int = 0,
) -> Generator[tuple[T, int], Any, Any]:
    ...


@overload
def _extraction_generator(
    query: Sequence[Any],
    choices: dict[Sequence[T], Any],
    scorer: Callable[[Sequence[Any], Sequence[Any]], int] = quick_ratio,
    score_cutoff: int = 0,
) -> Generator[tuple[T, int, Any], Any, Any]:
    ...


def _extraction_generator(
    query: Sequence[Any],
    choices: Sequence[T] | dict[Sequence[T], Any],
    scorer: Callable[[Sequence[Any], Sequence[Any]], int] = quick_ratio,
    score_cutoff: int = 0,
):
    try:
        for key, value in choices.items():  # type: ignore  # this is okay.
            score = scorer(query, key)
            if score >= score_cutoff:
                yield (key, score, value)
    except AttributeError:
        for choice in choices:
            score = scorer(query, choice)  # type: ignore # TODO: figure this out later
            if score >= score_cutoff:
                yield (choice, score)


def extract(
    query: Sequence[Any],
    choices: Sequence[Any] | dict[Sequence[Any], Any],
    *,
    scorer: Callable[[Sequence[Any], Sequence[Any]], int] = quick_ratio,
    score_cutoff: int = 0,
    limit: int | None = 10
):
    it = _extraction_generator(query, choices, scorer, score_cutoff)

    def key(t):
        return t[1]

    if limit is not None:
        return heapq.nlargest(limit, it, key=key)
    return sorted(it, key=key, reverse=True)


def extract_one(
    query: Sequence[Any],
    choices: Sequence[Any] | dict[Sequence[Any], Any],
    *,
    scorer: Callable[[Sequence[Any], Sequence[Any]], int] = quick_ratio,
    score_cutoff: int = 0
) -> tuple[Any, int] | tuple[Any, int, Any] | None:
    it = _extraction_generator(query, choices, scorer, score_cutoff)

    def key(t):
        return t[1]

    try:
        return max(it, key=key)
    except:
        # iterator could return nothing
        return None


def extract_or_exact(
    query: Sequence[Any],
    choices: Sequence[Any] | dict[Sequence[Any], Any],
    *,
    limit: int | None = None,
    scorer: Callable[[Sequence[Any], Sequence[Any]], int] = quick_ratio,
    score_cutoff: int = 0
) -> list[tuple[Any, int] | tuple[Any, int, Any]]:
    matches = extract(query, choices, scorer=scorer, score_cutoff=score_cutoff, limit=limit)
    if len(matches) == 0:
        return []

    if len(matches) == 1:
        return matches

    top = matches[0][1]
    second = matches[1][1]

    # check if the top one is exact or more than 30% more correct than the top
    if top == 100 or top > (second + 30):
        return [matches[0]]

    return matches


def extract_matches(
    query: Sequence[Any],
    choices: Sequence[Any] | dict[Sequence[Any], Any],
    *,
    scorer: Callable[[Sequence[Any], Sequence[Any]], int] = quick_ratio,
    score_cutoff: int = 0
):
    matches = extract(query, choices, scorer=scorer, score_cutoff=score_cutoff, limit=None)
    if len(matches) == 0:
        return []

    top_score = matches[0][1]
    to_return = []
    index = 0
    while True:
        try:
            match = matches[index]
        except IndexError:
            break
        else:
            index += 1

        if match[1] != top_score:
            break

        to_return.append(match)
    return to_return


@overload
def finder(
    text: str, collection: Iterable[Any], *, key: Callable[[T], T] | None = ..., lazy: Literal[True]
) -> Generator[Any, None, None]:
    ...


@overload
def finder(text: str, collection: Iterable[Any], *, key: Callable[[T], T] | None = ..., lazy: Literal[False]) -> list[Any]:
    ...


@overload
def finder(
    text: str, collection: Iterable[Any], *, key: Callable[[T], T] | None = ..., lazy: bool = ...
) -> Generator[Any, None, None]:
    ...


def finder(text: str, collection: Iterable[Any], *, key: Callable[[T], T] | None = None, lazy: bool = True):
    suggestions: list[tuple[int, int, Any]] = []
    text = str(text)
    pat = ".*?".join(map(re.escape, text))
    regex = re.compile(pat, flags=re.IGNORECASE)
    for item in collection:
        to_search = key(item) if key else item
        r = regex.search(to_search)
        if r:
            suggestions.append((len(r.group()), r.start(), item))

    def sort_key(tup):
        if key:
            return tup[0], tup[1], key(tup[2])
        return tup

    if lazy:
        return (z for _, _, z in sorted(suggestions, key=sort_key))
    else:
        return [z for _, _, z in sorted(suggestions, key=sort_key)]


def find(text: str, collection: Iterable[Any], *, key: Callable[[T], T] | None = None) -> Any | None:
    try:
        found: list[Any] = finder(text, collection, key=key, lazy=False)
        return found[0]
    except IndexError:
        return None
