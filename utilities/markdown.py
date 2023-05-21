from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from functools import wraps
from typing import Concatenate, ParamSpec, TypeVar

from yarl import URL


M = TypeVar("M", bound="MarkdownBuilder")
P = ParamSpec("P")
StrOrUrl = TypeVar("StrOrUrl", str, URL)

__all__ = ("MarkdownBuilder",)


def clamp(value: int, /, max_: int, min_: int) -> int:
    return min(max(min_, value), max_)


def after_markdown(func: Callable[Concatenate[M, P], None]) -> Callable[Concatenate[M, P], None]:
    @wraps(func)
    def wrapper(item: M, *args: P.args, **kwargs: P.kwargs) -> None:
        func(item, *args, **kwargs)
        item._inner += "\n"

    return wrapper


class MarkdownBuilder:
    def __init__(self) -> None:
        self._inner: str = str()

    @property
    def text(self) -> str:
        return self._inner

    @text.getter
    def text(self) -> str:
        c = deepcopy(self._inner)
        self.clear()
        return c

    @after_markdown
    def add_header(self, *, text: str, depth: int = 1) -> None:
        depth = clamp(depth, 5, 1)
        self._inner += "#" * depth
        self._inner += " " + text

    @after_markdown
    def add_link(self, *, url: StrOrUrl, text: str) -> None:
        self._inner += f"[{text}]({url})"

    @after_markdown
    def add_bulletpoints(self, *, texts: list[str]) -> None:
        builder = ""
        for item in texts:
            builder += f" - {item}\n"

        self._inner += builder

    @after_markdown
    def add_text(self, *, text: str) -> None:
        self._inner += text

    @after_markdown
    def add_newline(self, *, amount: int = 1) -> None:
        self._inner += "\n" * amount

    def clear(self) -> None:
        self._inner = str()
