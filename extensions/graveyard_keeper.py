from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, ClassVar, NamedTuple

import bs4
from discord import app_commands
from discord.ext import commands
from jishaku.functools import executor_function

from utilities.shared.fuzzy import finder

if TYPE_CHECKING:
    from bot import Mipha
    from utilities.context import Interaction

LOGGER = logging.getLogger(__name__)
NEXT_PAGE_PATTERN: re.Pattern[str] = re.compile(r"^Next page")


class GYKWikiPage(NamedTuple):
    label: str
    url: str

    def to_choice(self) -> app_commands.Choice[str]:
        return app_commands.Choice(name=self.label, value=self.url)


class GYK(commands.GroupCog, name="graveyard_keeper"):
    BASE_URL: ClassVar[str] = "https://graveyardkeeper.fandom.com/wiki/Special:AllPages"
    ROOT_PAGE: ClassVar[str] = "https://graveyardkeeper.fandom.com{href}"

    def __init__(self, bot: Mipha, /) -> None:
        self.bot: Mipha = bot
        self.cached_pages: list[GYKWikiPage] = []
        self.cached_choices: list[app_commands.Choice[str]] = []

    async def cog_load(self) -> None:
        LOGGER.info("[GYK] :: Beginning local cache of webpages.")
        soups = await self._fetch_index_pages()
        filtered_pages = await self._filter_all_items(soups)
        LOGGER.info("[GYK] :: Finished caching wiki.")

        self.cached_pages = filtered_pages
        self.cached_choices = [page.to_choice() for page in self.cached_pages]

    async def _fetch_index_pages(self) -> list[bs4.BeautifulSoup]:
        ret: list[bs4.BeautifulSoup] = []
        index_url = self.BASE_URL
        async with self.bot.session.get(index_url) as resp:
            resp.raise_for_status()
            data = await resp.read()

        soup = bs4.BeautifulSoup(data, features="lxml")
        ret.append(soup)

        while True:
            try:
                next_slug = await self.find_next_index_url(soup)
                LOGGER.debug("[GYK] :: Found next slug: %r", next_slug)
                index_url = self.ROOT_PAGE.format(href=next_slug)
                LOGGER.debug("[GYK] :: Loading url %r", index_url)
            except ValueError:
                break

            async with self.bot.session.get(index_url) as resp:
                resp.raise_for_status()
                data = await resp.read()

            soup = bs4.BeautifulSoup(data, features="lxml")
            ret.append(soup)

        return ret

    @executor_function
    def find_next_index_url(self, soup: bs4.BeautifulSoup) -> str:
        nav = soup.find(name="a", string=NEXT_PAGE_PATTERN)  # pyright: ignore[reportArgumentType, reportCallIssue] # shitty typeshed
        if not nav or not isinstance(nav, bs4.Tag):
            raise ValueError("Soup was not valid.")

        return str(nav["href"])

    @executor_function
    def _filter_all_items(self, root_pages: list[bs4.BeautifulSoup], /) -> list[GYKWikiPage]:
        """
        HTML Structure at the time of writing is
        <div class=mw-allpages-body>
          <ul>
            <li>
              <a href=... title=...>
            </li>
            <li>
              ...
            </li>
            ...
          </ul>
        </div>
        """
        ret: list[GYKWikiPage] = []
        for root_page in root_pages:
            index_table = root_page.find("div", class_="mw-allpages-body")
            if not index_table or not isinstance(index_table, bs4.Tag):
                raise ValueError("Did we get a malformed page response?")

            table_list = index_table.select("ul")
            if not table_list:
                raise ValueError("Good page but we got a malformed table?")
            cleaned = table_list[0]

            for item in cleaned:
                item_anchor = item.find_next("a")
                if not item_anchor or not isinstance(item_anchor, bs4.Tag):
                    continue
                label, url = item_anchor.get("title"), item_anchor.get("href")
                assert isinstance(label, str)
                assert isinstance(url, str)
                clean_url = self.ROOT_PAGE.format(href=url)

                ret.append(GYKWikiPage(label, clean_url))

        ret = list(set(ret))  # dedupe
        ret.sort(key=lambda w: w.label)
        return ret

    def find_wiki_pages(self, query: str) -> list[GYKWikiPage]:
        return finder(query, self.cached_pages, key=lambda p: p.label)

    @app_commands.command()
    @app_commands.describe(
        item="The item to search for (please choose one of the options)",
        suppress="If you want to suppress the embed from the webpage, or not.",
    )
    async def wiki(self, interaction: Interaction, item: str, suppress: bool = True) -> None:  # noqa: FBT001, FBT002
        """Select an item from the Graveyard Keeper wiki to get a link for it!"""
        await interaction.response.send_message(
            f"[Here]({item})'s the Graveyard Keeper wiki page you requested!", suppress_embeds=suppress
        )

    @wiki.autocomplete("item")
    async def wiki_autocomplete(self, _: Interaction, current: str) -> list[app_commands.Choice[str]]:
        if not current:
            return self.cached_choices[:25]

        return [page.to_choice() for page in self.find_wiki_pages(current)[:25]]


async def setup(bot: Mipha, /) -> None:
    await bot.add_cog(GYK(bot))
