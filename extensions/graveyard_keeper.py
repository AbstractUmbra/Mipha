from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, NamedTuple

import bs4
from discord import app_commands
from discord.ext import commands
from jishaku.functools import executor_function

from utilities.shared.fuzzy import finder

if TYPE_CHECKING:
    from bot import Mipha
    from utilities.context import Interaction


class GYKWikiPage(NamedTuple):
    label: str
    url: str

    def to_choice(self) -> app_commands.Choice[str]:
        return app_commands.Choice(name=self.label, value=self.url)


class GYK(commands.GroupCog, name="graveyard_keeper"):
    ROOT_PAGE: ClassVar[str] = "https://graveyardkeeper.fandom.com{href}"
    INDEX_URLS: ClassVar[list[str]] = [
        "https://graveyardkeeper.fandom.com/wiki/Special:AllPages",
        "https://graveyardkeeper.fandom.com/wiki/Special:AllPages?from=Fence+supplies+For+Garden",
        "https://graveyardkeeper.fandom.com/wiki/Special:AllPages?from=Potters+wheel",
        "https://graveyardkeeper.fandom.com/wiki/Special:AllPages?from=Zombie+Brewery",
    ]

    def __init__(self, bot: Mipha, /) -> None:
        self.bot: Mipha = bot
        self.cached_pages: list[GYKWikiPage] = []
        self.cached_choices: list[app_commands.Choice[str]] = []

    async def cog_load(self) -> None:
        soups = await self._fetch_index_pages()
        filtered_pages = await self._filter_all_items(soups)

        self.cached_pages = filtered_pages
        self.cached_choices = [page.to_choice() for page in self.cached_pages]

    async def _cache_pages(self, items: list[GYKWikiPage], /) -> None:
        query = """
                """

        await self.bot.pool.executemany(query, items)

    async def _fetch_index_pages(self) -> list[bs4.BeautifulSoup]:
        ret: list[bs4.BeautifulSoup] = []
        for url in self.INDEX_URLS:
            async with self.bot.session.get(url) as resp:
                data = await resp.read()

            ret.append(bs4.BeautifulSoup(data, features="lxml"))

        return ret

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
    async def wiki_autocomplete(self, interaction: Interaction, current: str) -> list[app_commands.Choice[str]]:
        if not current:
            return self.cached_choices[:25]

        return [page.to_choice() for page in self.find_wiki_pages(current)[:25]]


async def setup(bot: Mipha, /) -> None:
    await bot.add_cog(GYK(bot))
