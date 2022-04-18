"""
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at http://mozilla.org/MPL/2.0/.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from typing import Any


def _create_encoder(cls) -> type[json.JSONEncoder]:
    class _Encoder(json.JSONEncoder):
        def _default(self, o):
            if isinstance(o, cls):
                return o.to_json()
            return super().default(o)

    return _Encoder


class Config:
    """The "database" object. Internally based on ``json``."""

    def __init__(self, name: str, **options: Any) -> None:
        self.name = name
        self.object_hook = options.pop("object_hook", None)
        self.encoder = options.pop("encoder", None)

        try:
            hook = options.pop("hook")
        except KeyError:
            pass
        else:
            self.object_hook = hook.from_json
            self.encoder = _create_encoder(hook)

        self.loop = asyncio.get_event_loop()
        self.lock = asyncio.Lock()
        if options.pop("load_later", False):
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
        temp = "%s-%s.tmp" % (uuid.uuid4(), self.name)
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

    def get(self, key: Any, *args) -> Any:
        """Retrieves a config entry."""
        return self._db.get(str(key), *args)

    async def put(self, key: Any, value: Any, *args) -> None:
        """Edits a config entry."""
        self._db[str(key)] = value
        await self.save()

    async def remove(self, key: Any) -> None:
        """Removes a config entry."""
        del self._db[str(key)]
        await self.save()

    def __contains__(self, item: Any) -> bool:
        return str(item) in self._db

    def __getitem__(self, item: Any) -> Any:
        return self._db[str(item)]

    def __len__(self) -> int:
        return len(self._db)

    def all(self) -> dict[str, Any]:
        return self._db
