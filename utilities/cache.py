"""
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at http://mozilla.org/MPL/2.0/.
"""

from __future__ import annotations

import asyncio
import enum
import inspect
import time
from collections.abc import Awaitable, Callable, Coroutine, MutableMapping
from functools import wraps
from typing import Any, Literal, ParamSpec, Protocol, TypeVar, overload

from lru import LRU


P = ParamSpec("P")
K = TypeVar("K")
V = TypeVar("V")
T = TypeVar("T")
T_co = TypeVar("T_co", covariant=True)


def _wrap_and_store_coroutine(cache: dict[K, V] | ExpiringCache | LRU, key: K, coro: Awaitable[V]) -> Awaitable[V]:
    async def func() -> V:
        value = await coro
        cache[key] = value
        return value

    return func()


def _wrap_new_coroutine(value: T) -> Awaitable[T]:
    async def new_coroutine() -> T:
        return value

    return new_coroutine()


class ExpiringCache(dict[K, tuple[V, float]]):
    def __init__(self, seconds: float) -> None:
        self.__ttl = seconds
        super().__init__()

    def __verify_cache_integrity(self) -> None:
        # Have to do this in two steps...
        current_time = time.monotonic()
        to_remove = [k for (k, (_, t)) in self.items() if current_time > (t + self.__ttl)]
        for k in to_remove:
            del self[k]

    def __contains__(self, key: object) -> bool:
        self.__verify_cache_integrity()
        return super().__contains__(key)

    def __getitem__(self, key: K) -> tuple[V, float]:
        self.__verify_cache_integrity()
        return super().__getitem__(key)

    def __setitem__(self, key: K, value: V) -> None:
        super().__setitem__(key, (value, time.monotonic()))


class Strategy(enum.Enum):
    lru = 1
    raw = 2
    timed = 3


class _Cache(Protocol[P, T]):
    cache: MutableMapping[str, T]

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> T:
        ...

    def invalidate(self, *args: Any, **kwargs: Any) -> bool:
        ...

    def invalidate_containing(self, key: Any) -> None:
        ...

    def get_key(self, *args: P.args, **kwargs: P.kwargs) -> str:
        ...

    def get_stats(self) -> tuple[int, int]:
        ...


class _ExpiringCache(_Cache[P, T], Protocol[P, T]):
    cache: MutableMapping[str, tuple[T, float]]


class _BaseCacheDecorator(Protocol):
    @overload
    def __call__(self, __func: Callable[P, Coroutine[Any, Any, T]]) -> _Cache[P, Awaitable[T]]:
        ...

    @overload
    def __call__(self, __func: Callable[P, T]) -> _Cache[P, T]:
        ...


class _ExpiringCacheDecorator(Protocol):
    @overload
    def __call__(self, __func: Callable[P, Coroutine[Any, Any, T]]) -> _ExpiringCache[P, Awaitable[T]]:
        ...

    @overload
    def __call__(self, __func: Callable[P, T]) -> _ExpiringCache[P, T]:
        ...


@overload
def cache(
    maxsize: int = ...,
    strategy: Literal[Strategy.lru, Strategy.raw] = ...,
    ignore_kwargs: bool = ...,
) -> _BaseCacheDecorator:
    ...


@overload
def cache(
    maxsize: int = ...,
    strategy: Literal[Strategy.timed] = ...,
    ignore_kwargs: bool = ...,
) -> _ExpiringCacheDecorator:
    ...


@overload
def cache(
    maxsize: int = ...,
    strategy: Strategy = ...,
    ignore_kwargs: bool = ...,
) -> _BaseCacheDecorator | _ExpiringCacheDecorator:
    ...


def cache(
    maxsize: int = 128,
    strategy: Strategy = Strategy.lru,
    ignore_kwargs: bool = False,
) -> _BaseCacheDecorator | _ExpiringCacheDecorator:
    @overload
    def decorator(func: Callable[P, T]) -> _Cache[P, T] | _ExpiringCache[P, T]:
        ...

    @overload
    def decorator(func: Callable[P, Coroutine[Any, Any, T]]) -> _Cache[P, Awaitable[T]] | _ExpiringCache[P, Awaitable[T]]:
        ...

    def decorator(
        func: Callable[P, T] | Callable[P, Coroutine[Any, Any, T]]
    ) -> _Cache[P, T] | _Cache[P, Awaitable[T]] | _ExpiringCache[P, T] | _ExpiringCache[P, Awaitable[T]]:
        if strategy is Strategy.lru:
            _internal_cache = LRU(maxsize)
            _stats = _internal_cache.get_stats

        elif strategy is Strategy.raw:
            _internal_cache = {}
            _stats = lambda: (0, 0)

        elif strategy is Strategy.timed:
            _internal_cache = ExpiringCache(maxsize)
            _stats = lambda: (0, 0)

        def _make_key(args: tuple[Any, ...], kwargs: dict[Any, Any]) -> str:
            # this is a bit of a cluster fuck
            # we do care what 'self' parameter is when we __repr__ it
            def _true_repr(o) -> str:
                if o.__class__.__repr__ is object.__repr__:
                    return f"<{o.__class__.__module__}.{o.__class__.__name__}>"
                return repr(o)

            key: list[str] = [f"{func.__module__}.{func.__name__}"]
            key.extend(_true_repr(o) for o in args)
            if not ignore_kwargs:
                for k, v in kwargs.items():
                    # note: this only really works for this use case in particular
                    # I want to pass asyncpg.Connection objects to the parameters
                    # however, they use default __repr__ and I do not care what
                    # connection is passed in, so I needed a bypass.
                    if k == "connection":
                        continue

                    key.append(_true_repr(k))
                    key.append(_true_repr(v))

            return ":".join(key)

        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T | Awaitable[T] | tuple[T, float] | Awaitable[tuple[T, float]]:
            key = _make_key(args, kwargs)
            try:
                value = _internal_cache[key]
            except KeyError:
                value = func(*args, **kwargs)

                if inspect.isawaitable(value):
                    return _wrap_and_store_coroutine(_internal_cache, key, value)

                _internal_cache[key] = value
                return value
            else:
                if asyncio.iscoroutinefunction(func):
                    return _wrap_new_coroutine(value)
                return value

        def _invalidate(*args: P.args, **kwargs: P.kwargs) -> bool:
            try:
                del _internal_cache[_make_key(args, kwargs)]
            except KeyError:
                return False
            else:
                return True

        def _invalidate_containing(key: str) -> None:
            to_remove = []
            for k in _internal_cache.keys():
                if key in k:
                    to_remove.append(k)
            for k in to_remove:
                try:
                    del _internal_cache[k]
                except KeyError:
                    continue

        wrapper.cache = _internal_cache
        wrapper.get_key = lambda *args, **kwargs: _make_key(args, kwargs)
        wrapper.invalidate = _invalidate
        wrapper.get_stats = _stats
        wrapper.invalidate_containing = _invalidate_containing
        return wrapper  # type: ignore # can't be done

    return decorator
