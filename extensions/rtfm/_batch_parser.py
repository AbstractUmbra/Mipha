from __future__ import annotations

import asyncio
import collections
import logging
from collections import defaultdict, deque
from contextlib import suppress
from operator import attrgetter
from typing import TYPE_CHECKING, NamedTuple

import discord
from bs4 import BeautifulSoup

from utilities.shared import scheduling

from . import doc_cache, docs
from ._parsing import get_symbol_markdown
from ._redis_cache import StaleItemCounter

if TYPE_CHECKING:
    from bot import Mipha

LOGGER = logging.getLogger(__name__)
LOGGER.setLevel(logging.DEBUG)


class StaleInventoryNotifier:
    """Handle sending notifications about stale inventories through `DocItem`s to dev log."""

    symbol_counter = StaleItemCounter()

    def __init__(self, bot: Mipha) -> None:
        self.bot: Mipha = bot
        self._init_task = scheduling.create_task(self._init_channel(), name="StaleInventoryNotifier channel init")
        self._warned_urls = set()

    async def _init_channel(self) -> None:
        """Wait for guild and get channel."""
        await self.bot.wait_until_ready()
        self._dev_log: discord.TextChannel = self.bot.get_channel(710249355600330754)  # type: ignore

    async def send_warning(self, doc_item: docs.DocItem) -> None:
        """Send a warning to dev log if one wasn't already sent for `item`'s url."""
        if doc_item.url not in self._warned_urls and await self.symbol_counter.increment_for(doc_item) < 3:
            # Only warn if the item got less than 3 warnings
            # or if it has been more than 3 weeks since the last warning
            self._warned_urls.add(doc_item.url)
            await self._init_task
            embed = discord.Embed(
                description=f"Doc item `{doc_item.symbol_id=}` present in loaded documentation inventories "
                f"not found on [site]({doc_item.url}), inventories may need to be refreshed."
            )
            await self._dev_log.send(embed=embed)


class QueueItem(NamedTuple):
    """Contains a `DocItem` and the `BeautifulSoup` object needed to parse it."""

    doc_item: docs.DocItem
    soup: BeautifulSoup

    def __eq__(self, other: QueueItem | docs.DocItem) -> bool:
        if isinstance(other, docs.DocItem):
            return self.doc_item == other
        return NamedTuple.__eq__(self, other)


class ParseResultFuture(asyncio.Future):
    """
    Future with metadata for the parser class.

    `user_requested` is set by the parser when a Future is requested by an user and moved to the front,
    allowing the futures to only be waited for when clearing if they were user requested.
    """

    def __init__(self) -> None:
        super().__init__()
        self.user_requested = False


class BatchParser:
    """
    Get the Markdown of all symbols on a page and send them to redis when a symbol is requested.

    DocItems are added through the `add_item` method which adds them to the `_page_doc_items` dict.
    `get_markdown` is used to fetch the Markdown; when this is used for the first time on a page,
    all of the symbols are queued to be parsed to avoid multiple web requests to the same page.
    """

    def __init__(self, bot: Mipha) -> None:
        self.bot: Mipha = bot
        self._queue: deque[QueueItem] = collections.deque()
        self._page_doc_items: dict[str, list[docs.DocItem]] = defaultdict(list)
        self._item_futures: dict[docs.DocItem, ParseResultFuture] = defaultdict(ParseResultFuture)
        self._parse_task = None

        self.stale_inventory_notifier = StaleInventoryNotifier(self.bot)

    async def get_markdown(self, doc_item: docs.DocItem) -> str | None:
        """
        Get the result Markdown of `doc_item`.

        If no symbols were fetched from `doc_item`s page before,
        the HTML has to be fetched and then all items from the page are put into the parse queue.

        Not safe to run while `self.clear` is running.
        """
        if doc_item not in self._item_futures and doc_item not in self._queue:
            self._item_futures[doc_item].user_requested = True

            async with self.bot.session.get(doc_item.url, raise_for_status=True) as response:
                soup = await self.bot.loop.run_in_executor(
                    None,
                    BeautifulSoup,
                    await response.text(encoding="utf8"),
                    "lxml",
                )

            self._queue.extendleft(QueueItem(item, soup) for item in self._page_doc_items[doc_item.url])
            LOGGER.debug("Added items from %s to the parse queue.", doc_item.url)

            if self._parse_task is None:
                self._parse_task = scheduling.create_task(self._parse_queue(), name="Queue parse")
        else:
            self._item_futures[doc_item].user_requested = True
        with suppress(ValueError):
            # If the item is not in the queue then the item is already parsed or is being parsed
            self._move_to_front(doc_item)
        return await self._item_futures[doc_item]

    async def _parse_queue(self) -> None:
        """
        Parse all items from the queue, setting their result Markdown on the futures and sending them to redis.

        The coroutine will run as long as the queue is not empty, resetting `self._parse_task` to None when finished.
        """
        LOGGER.debug("Starting queue parsing.")
        try:
            while self._queue:
                item, soup = self._queue.pop()
                markdown = None

                if (future := self._item_futures[item]).done():
                    # Some items are present in the inventories multiple times under different symbol names,
                    # if we already parsed an equal item, we can just skip it.
                    continue

                try:
                    markdown = await self.bot.loop.run_in_executor(None, get_symbol_markdown, soup, item)
                    if markdown is not None:
                        await doc_cache.set(item, markdown)
                    else:
                        # Don't wait for this coro as the parsing doesn't depend on anything it does.
                        scheduling.create_task(
                            self.stale_inventory_notifier.send_warning(item), name="Stale inventory warning"
                        )
                except Exception:
                    LOGGER.exception("Unexpected error when handling %s", item)
                future.set_result(markdown)
                del self._item_futures[item]
                await asyncio.sleep(0.1)
        finally:
            self._parse_task = None
            LOGGER.debug("Finished parsing queue.")

    def _move_to_front(self, item: QueueItem | docs.DocItem) -> None:
        """Move `item` to the front of the parse queue."""
        # The parse queue stores soups along with the doc symbols in QueueItem objects,
        # in case we're moving a DocItem we have to get the associated QueueItem first and then move it.
        item_index = self._queue.index(item)  # type: ignore
        queue_item = self._queue[item_index]
        del self._queue[item_index]

        self._queue.append(queue_item)
        LOGGER.debug("Moved %s to the front of the queue.", item)

    def add_item(self, doc_item: docs.DocItem) -> None:
        """Map a DocItem to its page so that the symbol will be parsed once the page is requested."""
        self._page_doc_items[doc_item.url].append(doc_item)

    async def clear(self) -> None:
        """
        Clear all internal symbol data.

        Wait for all user-requested symbols to be parsed before clearing the parser.
        """
        for future in filter(attrgetter("user_requested"), self._item_futures.values()):
            await future
        if self._parse_task is not None:
            self._parse_task.cancel()
        self._queue.clear()
        self._page_doc_items.clear()
        self._item_futures.clear()
