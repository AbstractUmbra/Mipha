from __future__ import annotations

import asyncio
import logging
import sys
import textwrap
from collections import defaultdict
from types import SimpleNamespace
from typing import TYPE_CHECKING, Annotated, Literal, NamedTuple

import aiohttp
import discord
from discord.ext import commands

from utilities.shared.locks import SharedEvent, lock
from utilities.shared.paginator import LinePaginator
from utilities.shared.scheduling import Scheduler

from . import NAMESPACE, PRIORITY_PACKAGES, _batch_parser, doc_cache
from ._converters import Inventory, PackageName, ValidURL  # noqa: TCH001
from ._inventory_parser import InvalidHeaderError, InventoryDict, fetch_inventory

if TYPE_CHECKING:
    from bot import Mipha
    from utilities._types.config import RTFMConfig
    from utilities.context import Context
    from utilities.shared.async_config import Config

LOGGER = logging.getLogger(__name__)
LOGGER.setLevel(logging.DEBUG)

# symbols with a group contained here will get the group prefixed on duplicates
FORCE_PREFIX_GROUPS = (
    "term",
    "label",
    "token",
    "doc",
    "pdbcommand",
    "2to3fixer",
)
# Delay to wait before trying to reach a rescheduled inventory again, in minutes
FETCH_RESCHEDULE_DELAY = SimpleNamespace(first=2, repeated=5)

COMMAND_LOCK_SINGLETON = "inventory refresh"


class DocItem(NamedTuple):
    """Holds inventory symbol information."""

    package: str  # Name of the package name the symbol is from
    group: str  # Interpshinx "role" of the symbol, for example `label` or `method`
    base_url: str  # Absolute path to to which the relative path resolves, same for all items with the same package
    relative_url_path: str  # Relative path to the page where the symbol is located
    symbol_id: str  # Fragment id used to locate the symbol on the page

    @property
    def url(self) -> str:
        """Return the absolute url to the symbol."""
        return self.base_url + self.relative_url_path


class DocCog(commands.Cog):
    """A set of commands for querying & displaying documentation."""

    def __init__(self, bot: Mipha, *, config: Config[RTFMConfig]) -> None:
        # Contains URLs to documentation home pages.
        # Used to calculate inventory diffs on refreshes and to display all currently stored inventories.
        self.bot = bot
        self._config = config
        self.base_urls = {}
        self.doc_symbols: dict[str, DocItem] = {}  # Maps symbol names to objects containing their metadata.
        self.item_fetcher = _batch_parser.BatchParser(self.bot)
        # Maps a conflicting symbol name to a list of the new, disambiguated names created from conflicts with the name.
        self.renamed_symbols = defaultdict(list)

        self.inventory_scheduler = Scheduler(self.__class__.__name__)

        self.refresh_event = asyncio.Event()
        self.refresh_event.set()
        self.symbol_get_event = SharedEvent()

        bot.loop.create_task(self.refresh_inventories(), name="Docs refreshing inventories")

    def update_single(self, package_name: str, base_url: str, inventory: InventoryDict) -> None:
        """
        Build the inventory for a single package.

        Where:
            * `package_name` is the package name to use in logs and when qualifying symbols
            * `base_url` is the root documentation URL for the specified package, used to build
                absolute paths that link to specific symbols
            * `package` is the content of a intersphinx inventory.
        """
        self.base_urls[package_name] = base_url

        for group, items in inventory.items():
            for symbol_name, relative_doc_url in items:
                # e.g. get 'class' from 'py:class'
                group_name = group.split(":")[1]
                symbol_name = self.ensure_unique_symbol_name(
                    package_name,
                    group_name,
                    symbol_name,
                )

                relative_url_path, _, symbol_id = relative_doc_url.partition("#")
                # Intern fields that have shared content so we're not storing unique strings for every object
                doc_item = DocItem(
                    package_name,
                    sys.intern(group_name),
                    base_url,
                    sys.intern(relative_url_path),
                    symbol_id,
                )
                self.doc_symbols[symbol_name] = doc_item
                self.item_fetcher.add_item(doc_item)

        LOGGER.debug("Fetched inventory for %s.", package_name)

    async def update_or_reschedule_inventory(
        self,
        api_package_name: str,
        base_url: str,
        inventory_url: str,
    ) -> None:
        """
        Update the cog's inventories, or reschedule this method to execute again if the remote inventory is unreachable.

        The first attempt is rescheduled to execute in `FETCH_RESCHEDULE_DELAY.first` minutes, the subsequent attempts
        in `FETCH_RESCHEDULE_DELAY.repeated` minutes.
        """
        try:
            package = await fetch_inventory(inventory_url, session=self.bot.session)
        except InvalidHeaderError as e:
            # Do not reschedule if the header is invalid, as the request went through but the contents are invalid.
            LOGGER.warning("Invalid inventory header at %s. Reason: %s", inventory_url, e)
            return

        if not package:
            if api_package_name in self.inventory_scheduler:
                self.inventory_scheduler.cancel(api_package_name)
                delay = FETCH_RESCHEDULE_DELAY.repeated
            else:
                delay = FETCH_RESCHEDULE_DELAY.first
            LOGGER.info("Failed to fetch inventory; attempting again in %s minutes.", delay)
            self.inventory_scheduler.schedule_later(
                delay * 60,
                api_package_name,
                self.update_or_reschedule_inventory(api_package_name, base_url, inventory_url),
            )
        else:
            if not base_url:
                base_url = self.base_url_from_inventory_url(inventory_url)
            self.update_single(api_package_name, base_url, package)

    def ensure_unique_symbol_name(self, package_name: str, group_name: str, symbol_name: str) -> str:
        """
        Ensure `symbol_name` doesn't overwrite an another symbol in `doc_symbols`.

        For conflicts, rename either the current symbol or the existing symbol with which it conflicts.
        Store the new name in `renamed_symbols` and return the name to use for the symbol.

        If the existing symbol was renamed or there was no conflict, the returned name is equivalent to `symbol_name`.
        """
        if (item := self.doc_symbols.get(symbol_name)) is None:
            return symbol_name  # There's no conflict so it's fine to simply use the given symbol name.

        def rename(prefix: str, *, rename_extant: bool = False) -> str:
            new_name = f"{prefix}.{symbol_name}"
            if new_name in self.doc_symbols:
                # If there's still a conflict, qualify the name further.
                if rename_extant:
                    new_name = f"{item.package}.{item.group}.{symbol_name}"
                else:
                    new_name = f"{package_name}.{group_name}.{symbol_name}"

            self.renamed_symbols[symbol_name].append(new_name)

            if rename_extant:
                # Instead of renaming the current symbol, rename the symbol with which it conflicts.
                self.doc_symbols[new_name] = self.doc_symbols[symbol_name]
                return symbol_name
            return new_name

        # When there's a conflict, and the package names of the items differ, use the package name as a prefix.
        if package_name != item.package:
            if package_name in PRIORITY_PACKAGES:
                return rename(item.package, rename_extant=True)
            return rename(package_name)

        # If the symbol's group is a non-priority group from FORCE_PREFIX_GROUPS,
        # add it as a prefix to disambiguate the symbols.
        if group_name in FORCE_PREFIX_GROUPS:
            if item.group in FORCE_PREFIX_GROUPS:
                needs_moving = FORCE_PREFIX_GROUPS.index(group_name) < FORCE_PREFIX_GROUPS.index(item.group)
            else:
                needs_moving = False
            return rename(item.group if needs_moving else group_name, rename_extant=needs_moving)

        # If the above conditions didn't pass, either the existing symbol has its group in FORCE_PREFIX_GROUPS,
        # or deciding which item to rename would be arbitrary, so we rename the existing symbol.
        return rename(item.group, rename_extant=True)

    async def refresh_inventories(self) -> None:
        """Refresh internal documentation inventories."""
        await self.bot.wait_until_ready()

        self.refresh_event.clear()
        await self.symbol_get_event.wait()
        LOGGER.debug("Refreshing documentation inventory...")
        self.inventory_scheduler.cancel_all()

        self.base_urls.clear()
        self.doc_symbols.clear()
        self.renamed_symbols.clear()
        await self.item_fetcher.clear()

        coros = [
            self.update_or_reschedule_inventory(package["package"], package["base_url"], package["inventory_url"])
            for package in self._config["rtfm"]["packages"]
        ]
        await asyncio.gather(*coros)
        LOGGER.debug("Finished inventory refresh.")
        self.refresh_event.set()

    def get_symbol_item(self, symbol_name: str) -> tuple[str, DocItem | None]:
        """
        Get the `DocItem` and the symbol name used to fetch it from the `doc_symbols` dict.

        If the doc item is not found directly from the passed in name and the name contains a space,
        the first word of the name will be attempted to be used to get the item.
        """
        doc_item = self.doc_symbols.get(symbol_name)
        if doc_item is None and " " in symbol_name:
            symbol_name = symbol_name.split(maxsplit=1)[0]
            doc_item = self.doc_symbols.get(symbol_name)

        return symbol_name, doc_item

    async def get_symbol_markdown(self, doc_item: DocItem) -> str:
        """
        Get the Markdown from the symbol `doc_item` refers to.

        First a redis lookup is attempted, if that fails the `item_fetcher`
        is used to fetch the page and parse the HTML from it into Markdown.
        """
        markdown = await doc_cache.get(doc_item)

        if markdown is None:
            LOGGER.debug("Redis cache miss with %s.", doc_item)
            try:
                markdown = await self.item_fetcher.get_markdown(doc_item)

            except aiohttp.ClientError as e:
                LOGGER.warning("A network error has occurred when requesting parsing of %s.", doc_item, exc_info=e)
                return "Unable to parse the requested symbol due to a network error."

            except Exception:
                LOGGER.exception("An unexpected error has occurred when requesting parsing of %s.", doc_item)
                return "Unable to parse the requested symbol due to an error."

            if markdown is None:
                return "Unable to parse the requested symbol."

        return markdown

    async def create_symbol_embed(self, symbol_name: str) -> discord.Embed | None:
        """
        Attempt to scrape and fetch the data for the given `symbol_name`, and build an embed from its contents.

        If the symbol is known, an Embed with documentation about it is returned.

        First check the DocRedisCache before querying the cog's `BatchParser`.
        """
        LOGGER.debug("Building embed for symbol `%s`", symbol_name)
        if not self.refresh_event.is_set():
            LOGGER.debug("Waiting for inventories to be refreshed before processing item.")
            await self.refresh_event.wait()
        # Ensure a refresh can't run in case of a context switch until the with block is exited
        with self.symbol_get_event:
            symbol_name, doc_item = self.get_symbol_item(symbol_name)
            if doc_item is None:
                LOGGER.debug("Symbol does not exist.")
                return None

            # Show all symbols with the same name that were renamed in the footer,
            # with a max of 200 chars.
            if symbol_name in self.renamed_symbols:
                renamed_symbols = ", ".join(self.renamed_symbols[symbol_name])
                footer_text = textwrap.shorten("Similar names: " + renamed_symbols, 200, placeholder=" ...")
            else:
                footer_text = ""

            embed = discord.Embed(
                title=discord.utils.escape_markdown(symbol_name),
                url=f"{doc_item.url}#{doc_item.symbol_id}",
                description=await self.get_symbol_markdown(doc_item),
            )
            embed.set_footer(text=footer_text)
            return embed

    @commands.group(name="docs", aliases=("doc", "d", "rtfm"), invoke_without_command=True)
    async def docs_group(self, ctx: Context, *, symbol_name: str | None) -> None:
        """Look up documentation for Python symbols."""
        await self.get_command(ctx, symbol_name=symbol_name)

    @docs_group.command(name="getdoc", aliases=("g", "get"))
    async def get_command(self, ctx: Context, *, symbol_name: str | None) -> None:
        """
        Return a documentation embed for a given symbol.

        If no symbol is given, return a list of all available inventories.

        Examples:
            !docs
            !docs aiohttp
            !docs aiohttp.ClientSession
            !docs getdoc aiohttp.ClientSession
        """
        if not symbol_name:
            inventory_embed = discord.Embed(
                title=f"All inventories (`{len(self.base_urls)}` total)", colour=discord.Colour.blue()
            )

            lines = sorted(f"- [`{name}`]({url})" for name, url in self.base_urls.items())
            if self.base_urls:
                await LinePaginator.paginate(lines, ctx, inventory_embed, max_size=400, empty=False)

            else:
                inventory_embed.description = "Hmmm, seems like there's nothing here yet."
                await ctx.send(embed=inventory_embed)

        else:
            symbol = symbol_name.strip("`")
            async with ctx.typing():
                doc_embed = await self.create_symbol_embed(symbol)

            await ctx.send(embed=doc_embed)

    @staticmethod
    def base_url_from_inventory_url(inventory_url: str) -> str:
        """Get a base url from the url to an objects inventory by removing the last path segment."""
        return inventory_url.removesuffix("/").rsplit("/", maxsplit=1)[0] + "/"

    @docs_group.command(name="setdoc", aliases=("s",))
    @commands.is_owner()
    @lock(NAMESPACE, COMMAND_LOCK_SINGLETON, raise_error=True)  # type: ignore
    async def set_command(
        self,
        ctx: Context,
        package_name: Annotated[str, PackageName],
        inventory: Annotated[tuple[str, InventoryDict], Inventory],
        base_url: Annotated[str, ValidURL] = "",
    ) -> None:
        """
        Adds a new documentation metadata object to the site's database.

        The database will update the object, should an existing item with the specified `package_name` already exist.
        If the base url is not specified, a default created by removing the last segment of the inventory url is used.

        Example:
            !docs setdoc \
                    python \
                    https://docs.python.org/3/objects.inv
        """
        if base_url and not base_url.endswith("/"):
            raise commands.BadArgument("The base url must end with a slash.")
        inventory_url, inventory_dict = inventory
        body = {"package": package_name, "base_url": base_url, "inventory_url": inventory_url}

        LOGGER.info(
            "User @{ctx.author} ({ctx.author.id}) added a new documentation package:\n"  # noqa: G003
            + "\n".join(f"{key}: {value}" for key, value in body.items())
        )

        if not base_url:
            base_url = self.base_url_from_inventory_url(inventory_url)
        self.update_single(package_name, base_url, inventory_dict)
        await ctx.send(f"Added the package `{package_name}` to the database and updated the inventories.")

    @docs_group.command(name="deletedoc", aliases=("removedoc", "rm"))
    @commands.is_owner()
    @lock(NAMESPACE, COMMAND_LOCK_SINGLETON, raise_error=True)  # type: ignore
    async def delete_command(self, ctx: Context, package_name: Annotated[str, PackageName]) -> None:
        """
        Removes the specified package from the database.

        Example:
            !docs deletedoc aiohttp
        """
        async with ctx.typing():
            await self.refresh_inventories()
            await doc_cache.delete(package_name)
        await ctx.send(f"Successfully deleted `{package_name}` and refreshed the inventories.")

    @docs_group.command(name="refreshdoc", aliases=("rfsh", "r"))
    @lock(NAMESPACE, COMMAND_LOCK_SINGLETON, raise_error=True)  # type: ignore
    async def refresh_command(self, ctx: Context) -> None:
        """Refresh inventories and show the difference."""
        old_inventories = set(self.base_urls)
        async with ctx.typing():
            await self.refresh_inventories()
        new_inventories = set(self.base_urls)

        if added := ", ".join(new_inventories - old_inventories):
            added = "+ " + added

        if removed := ", ".join(old_inventories - new_inventories):
            removed = "- " + removed

        embed = discord.Embed(
            title="Inventories refreshed", description=f"```diff\n{added}\n{removed}```" if added or removed else ""
        )
        await ctx.send(embed=embed)

    @docs_group.command(name="cleardoccache", aliases=("deletedoccache",))
    async def clear_cache_command(self, ctx: Context, package_name: Annotated[str, PackageName] | Literal["*"]) -> None:
        """Clear the persistent redis cache for `package`."""
        if await doc_cache.delete(package_name):
            await self.item_fetcher.stale_inventory_notifier.symbol_counter.delete(package_name)
            await ctx.send(f"Successfully cleared the cache for `{package_name}`.")
        else:
            await ctx.send("No keys matching the package found.")

    async def cog_unload(self) -> None:
        """Clear scheduled inventories, queued symbols and cleanup task on cog unload."""
        self.inventory_scheduler.cancel_all()
        await self.item_fetcher.clear()
