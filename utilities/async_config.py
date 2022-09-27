"""
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at http://mozilla.org/MPL/2.0/.
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
from collections.abc import Callable
from typing import Any, Generic, TypeAlias, TypeVar, overload


ObjectHook: TypeAlias = Callable[[dict[str, Any]], Any]
_T = TypeVar("_T")
_defT = TypeVar("_defT")


class Config(Generic[_T]):
    """The "database" object. Internally based on ``json``."""

    def __init__(
        self,
        name: pathlib.Path,
        *,
        object_hook: ObjectHook | None = None,
        encoder: type[json.JSONEncoder] | None = None,
        load_later: bool = False,
    ) -> None:
        self.name = name
        self.object_hook = object_hook
        self.encoder = encoder
        self.loop = asyncio.get_event_loop()
        self.lock = asyncio.Lock()
        self._db: dict[str, _T] = {}

        if load_later:
            self.loop.create_task(self.load())
        else:
            self.load_from_file()

    def load_from_file(self) -> None:
        try:
            with open(self.name, "r") as f:
                self._db = json.load(f, object_hook=self.object_hook)
        except FileNotFoundError:
            self._db = {}

    async def load(self) -> None:
        async with self.lock:
            await self.loop.run_in_executor(None, self.load_from_file)

    def _dump(self) -> None:
        temp = self.name.with_suffix(".tmp")
        with open(temp, "w", encoding="utf-8") as tmp:
            json.dump(
                self._db.copy(),
                tmp,
                ensure_ascii=True,
                cls=self.encoder,
                separators=(",", ":"),
                indent=4,
            )

        # atomically move the file
        os.replace(temp, self.name)

    async def save(self) -> None:
        async with self.lock:
            await self.loop.run_in_executor(None, self._dump)

    @overload
    def get(self, key: Any) -> _T | Any | None:
        ...

    @overload
    def get(self, key: Any, default: _defT) -> _T | _defT:
        ...

    def get(self, key: Any, default: _defT = None) -> _T | _defT | None:
        """Retrieves a config entry."""
        return self._db.get(str(key), default)

    async def put(self, key: Any, value: _T | Any) -> None:
        """Edits a config entry."""
        self._db[str(key)] = value
        await self.save()

    async def remove(self, key: Any) -> None:
        """Removes a config entry."""
        del self._db[str(key)]
        await self.save()

    def __contains__(self, item: Any) -> bool:
        return str(item) in self._db

    def __getitem__(self, item: Any) -> _T | Any:
        return self._db[str(item)]

    def __len__(self) -> int:
        return len(self._db)

    def all(self) -> dict[str, _T]:
        return self._db
