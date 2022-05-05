"""
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at http://mozilla.org/MPL/2.0/.
"""

import codecs
import datetime
import json
import re
import sys
import unicodedata
from collections.abc import Iterable, Sequence
from typing import Any, SupportsAbs

from discord.utils import escape_markdown


CONTROL_CHARS = re.compile(
    "[%s]" % re.escape("".join(chr(i) for i in range(sys.maxunicode) if unicodedata.category(chr(i)).startswith("C")))
)


def group(iterable: Sequence[str], page_len: int = 50) -> list[str]:
    pages = []
    while iterable:
        pages.append(iterable[:page_len])
        iterable = iterable[page_len:]
    return pages


class plural:
    def __init__(self, value: SupportsAbs[int]) -> None:
        self.value = value

    def __format__(self, format_spec: str) -> str:
        v = self.value
        singular, _, plural = format_spec.partition("|")
        plural = plural or f"{singular}s"
        if abs(v) != 1:
            return f"{v} {plural}"
        return f"{v} {singular}"


def human_join(seq: Sequence[str], delim: str = ", ", final: str = "or") -> str:
    size = len(seq)
    if size == 0:
        return ""

    if size == 1:
        return seq[0]

    if size == 2:
        return f"{seq[0]} {final} {seq[1]}"

    return delim.join(seq[:-1]) + f" {final} {seq[-1]}"


class TabularData:
    def __init__(self) -> None:
        self._widths: list[int] = []
        self._columns: list[str] = []
        self._rows: list[list[str]] = []

    def set_columns(self, columns: list[str]) -> None:
        self._columns = columns
        self._widths = [len(c) + 2 for c in columns]

    def add_row(self, row: Iterable[Any]) -> None:
        rows = [str(r) for r in row]
        self._rows.append(rows)
        for index, element in enumerate(rows):
            width = len(element) + 2
            if width > self._widths[index]:
                self._widths[index] = width

    def add_rows(self, rows: Iterable[Iterable[Any]]) -> None:
        for row in rows:
            self.add_row(row)

    def render(self) -> str:
        """Renders a table in rST format.
        Example:
        +-------+-----+
        | Name  | Age |
        +-------+-----+
        | Alice | 24  |
        |  Bob  | 19  |
        +-------+-----+
        """

        sep = "+".join("-" * w for w in self._widths)
        sep = f"+{sep}+"

        to_draw = [sep]

        def get_entry(d):
            elem = "|".join(f"{e:^{self._widths[i]}}" for i, e in enumerate(d))
            return f"|{elem}|"

        to_draw.append(get_entry(self._columns))
        to_draw.append(sep)

        for row in self._rows:
            to_draw.append(get_entry(row))

        to_draw.append(sep)
        return "\n".join(to_draw)


def format_dt(dt: datetime.datetime, style: str | None = None) -> str:
    if style is None:
        return f"<t:{int(dt.timestamp())}>"
    return f"<t:{int(dt.timestamp())}:{style}>"


def to_codeblock(
    content: str,
    language: str = "py",
    replace_existing: bool = True,
    escape_md: bool = True,
    new: str = "'''",
) -> str:
    if replace_existing:
        content = content.replace("```", new)
    if escape_md:
        content = escape_markdown(content)
    return f"```{language}\n{content}\n```"


def escape_invis(decode_error: UnicodeDecodeError | UnicodeEncodeError) -> tuple[str | bytes, int] | None:
    if isinstance(decode_error, UnicodeDecodeError):
        return

    decode_error.end = decode_error.start + 1
    if CONTROL_CHARS.match(decode_error.object[decode_error.start : decode_error.end]):
        return codecs.backslashreplace_errors(decode_error)
    return (
        decode_error.object[decode_error.start : decode_error.end].encode("utf-8"),
        decode_error.end,
    )


codecs.register_error("escape-invis", escape_invis)  # type: ignore


def escape_invis_chars(content: str) -> str:
    """Escape invisible/control characters."""
    return content.encode("ascii", "escape-invis").decode("utf-8")


def clean_emojis(line: str) -> str:
    """Escape custom emojis."""
    return re.sub(r"<(a)?:([a-zA-Z0-9_]+):([0-9]+)>", "<\u200b\\1:\\2:\\3>", line)


def clean_single_backtick(line: str) -> str:
    """Clean string for insertion in single backtick code section.
    Clean backticks so we don't accidentally escape, and escape custom emojis
    that would be discordified.
    """
    if re.search("[^`]`[^`]", line) is not None:
        return "`%s`" % clean_double_backtick(line)
    if line[:2] == "``":
        line = "\u200b" + line
    if line[-1] == "`":
        line = line + "\u200b"
    return clean_emojis(line)


def clean_double_backtick(line: str) -> str:
    """Clean string for isnertion in double backtick code section.
    Clean backticks so we don't accidentally escape, and escape custom emojis
    that would be discordified.
    """
    line.replace("``", "`\u200b`")
    if line[0] == "`":
        line = "\u200b" + line
    if line[-1] == "`":
        line = line + "\u200b"

    return clean_emojis(line)


def clean_triple_backtick(line: str) -> str:
    """Clean string for insertion in triple backtick code section.
    Clean backticks so we don't accidentally escape, and escape custom emojis
    that would be discordified.
    """
    if not line:
        return line

    i = 0
    n = 0
    while i < len(line):
        if (line[i]) == "`":
            n += 1
        if n == 3:
            line = line[:i] + "\u200b" + line[i:]
            n = 1
            i += 1
        i += 1

    if line[-1] == "`":
        line += "\n"

    return clean_emojis(line)


def to_json(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=True)
