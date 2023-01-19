"""
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at http://mozilla.org/MPL/2.0/.
"""

from __future__ import annotations

import datetime
import logging
import secrets
from textwrap import shorten
from typing import TYPE_CHECKING, Callable, Coroutine

import discord
import hondana
from discord import app_commands
from discord.ext import commands, tasks
from discord.utils import as_chunks
from hondana.query import FeedOrderQuery, MangaListOrderQuery, Order
from typing_extensions import Self

from utilities import formats
from utilities.context import Context, Interaction
from utilities.paginator import MangaDexEmbed
from utilities.ui import MiphaBaseView


if TYPE_CHECKING:
    from bot import Mipha


LOG = logging.getLogger(__name__)


class MangaDexConverter(commands.Converter[hondana.Manga | hondana.Chapter | hondana.Author]):
    def lookup(
        self, bot: Mipha, item: str
    ) -> Callable[[str], Coroutine[None, None, hondana.Manga | hondana.Chapter | hondana.Author]] | None:
        table = {
            "title": bot.md_client.get_manga,
            "chapter": bot.md_client.get_chapter,
            "author": bot.md_client.get_author,
        }

        return table.get(item, None)

    async def convert(self, ctx: Context, argument: str) -> hondana.Manga | hondana.Chapter | hondana.Author | None:
        search = hondana.MANGADEX_URL_REGEX.search(argument)
        if search is None:
            return None

        item = self.lookup(ctx.bot, search["type"])
        if item is None:
            return None

        true_item = await item(search["ID"])
        return true_item


class MangaView(MiphaBaseView):
    def __init__(self, user: discord.abc.Snowflake, bot: Mipha, manga: list[hondana.Manga], /) -> None:
        self.user: discord.abc.Snowflake = user
        self.bot: Mipha = bot
        self.manga_id: str | None = None
        options: list[discord.SelectOption] = []
        for idx, mango in enumerate(manga, start=1):
            options.append(
                discord.SelectOption(label=f"[{idx}] {shorten(mango.title, width=95)}", description=mango.id, value=mango.id)
            )
        self._lookup = {m.id: m for m in manga}
        super().__init__()
        self.select.options = options

    @discord.ui.select(min_values=1, max_values=1, options=[])
    async def select(self, interaction: Interaction, item: discord.ui.Select[Self]) -> None:
        assert interaction.user is not None
        assert interaction.channel is not None

        is_nsfw = isinstance(interaction.channel, discord.PartialMessageable) or interaction.channel.is_nsfw()

        embed = await MangaDexEmbed.from_manga(self._lookup[item.values[0]], nsfw_allowed=is_nsfw)
        self.manga_id = item.values[0]
        if await self.bot.is_owner(interaction.user):
            self.follow.disabled = False

        await interaction.response.edit_message(content=None, embed=embed, view=self)

    @discord.ui.button(label="Follow?", disabled=True)
    async def follow(self, interaction: Interaction, _: discord.ui.Button[Self]) -> None:
        assert interaction.user is not None
        if not await self.bot.is_owner(interaction.user):
            raise commands.CheckFailure("You can't follow manga unless you're Umbra.")

        assert self.manga_id is not None
        await self.bot.md_client.follow_manga(self.manga_id)
        await interaction.response.send_message("You now follow this!", ephemeral=True)

    async def interaction_check(self, interaction: Interaction) -> bool:
        assert interaction.user is not None
        if self.user.id != interaction.user.id:
            raise app_commands.CheckFailure("You are not the owner of this interaction.")
        return True

    async def on_error(
        self, interaction: Interaction, error: discord.app_commands.AppCommandError, item: discord.ui.Item[Self]
    ) -> None:
        if isinstance(error, app_commands.CheckFailure):
            return await interaction.response.send_message("You can't choose someone else's Manga!", ephemeral=True)
        await super().on_error(interaction, error, item)


class MangaCog(commands.Cog, name="Manga"):
    """
    Cog to assist with Mangadex related things.
    """

    def __init__(self, bot: Mipha) -> None:
        self.bot: Mipha = bot
        self.webhook: discord.Webhook = discord.Webhook.from_url(bot.config.MANGADEX_WEBHOOK, session=bot.session)
        self.get_personal_feed.add_exception_type(hondana.APIException)
        self.get_personal_feed.start()

    mangadex_group = app_commands.Group(name="mangadex", description="commands for interacting with MangaDex!")

    @commands.group(aliases=["dex"])
    async def mangadex(self, ctx: Context) -> None:
        if not ctx.invoked_subcommand:
            return await ctx.send_help(self)

    @mangadex.command(name="get")
    async def get_(
        self,
        ctx: Context,
        *,
        item: hondana.Manga | hondana.Chapter | hondana.Author = commands.param(converter=MangaDexConverter),
    ) -> None:
        """
        This command takes a mangadex link to a chapter or manga and returns the data.
        """
        nsfw_allowed = isinstance(ctx.channel, discord.DMChannel) or ctx.channel.is_nsfw()

        if isinstance(item, hondana.Manga):
            embed = await MangaDexEmbed.from_manga(item)
        elif isinstance(item, hondana.Chapter):
            if item.chapter is None:
                await item.get_parent_manga()
            embed = await MangaDexEmbed.from_chapter(item, nsfw_allowed=nsfw_allowed)
        else:
            await ctx.send("Not found?")
            return

        await ctx.send(embed=embed)

    async def perform_search(self, search_query: str) -> list[hondana.Manga] | None:
        order = MangaListOrderQuery(relevance=Order.descending)

        collection = await self.bot.md_client.manga_list(limit=5, title=search_query, order=order)

        if not collection.manga:
            return

        return collection.manga

    @mangadex.command(name="search")
    async def search_(self, ctx: Context, *, search: str) -> None:
        """Search mangadex for a manga given it's name."""
        manga = await self.perform_search(search)
        if manga is None:
            await ctx.send("No results found!")
            return

        view = MangaView(ctx.author, ctx.bot, manga)
        await ctx.send(view=view)

    @mangadex_group.command(name="search")
    @app_commands.describe(query="The manga name to search for")
    async def slash_search(self, interaction: Interaction, query: str) -> None:
        """Search mangadex for a manga given it's name."""
        await interaction.response.defer()
        manga = await self.perform_search(query)
        if manga is None:
            await interaction.followup.send("No results found!", ephemeral=True)
            return

        view = MangaView(interaction.user, self.bot, manga)
        await interaction.followup.send(view=view, ephemeral=True)

    @search_.error
    async def search_error(self, ctx: Context, error: commands.CommandError) -> None:
        error = getattr(error, "original", error)
        if isinstance(error, ValueError):
            await ctx.send("You did not format the command flags properly.")
            return

    @mangadex.command(name="manga")
    async def manga_(self, ctx: Context, *, manga_id: str) -> None:
        """
        Uses a MangaDex UUID (for manga) to retrieve the data for it.
        """
        manga = await self.bot.md_client.get_manga(manga_id)

        if manga.content_rating in (
            hondana.ContentRating.pornographic,
            hondana.ContentRating.suggestive,
            hondana.ContentRating.erotica,
        ):
            await ctx.send("This manga is a bit too lewd for a non-lewd channel.")
            return

        embed = await MangaDexEmbed.from_manga(manga)

        await ctx.send(embed=embed)

    @mangadex_group.command(name="manga")
    @app_commands.describe(manga_id="The ID of the manga!")
    async def slash_manga(self, interaction: Interaction, manga_id: str) -> None:
        """Fetch details about a manga from MangaDex."""
        await interaction.response.defer()
        manga = await self.bot.md_client.get_manga(manga_id)

        if manga.content_rating in (
            hondana.ContentRating.pornographic,
            hondana.ContentRating.suggestive,
            hondana.ContentRating.erotica,
        ):
            await interaction.followup.send("This manga is a bit too lewd for a non-lewd channel.")
            return

        embed = await MangaDexEmbed.from_manga(manga)

        await interaction.followup.send(embed=embed)

    @mangadex.command(name="chapter")
    async def chapter_(self, ctx: Context, *, chapter_id: str) -> None:
        """
        Returns data on a MangaDex chapter.
        """
        chapter = await self.bot.md_client.get_chapter(chapter_id)

        if chapter.manga is None:
            await chapter.get_parent_manga()

        assert chapter.manga is not None

        nsfw_allowed = isinstance(ctx.channel, discord.DMChannel) or ctx.channel.is_nsfw()

        embed = await MangaDexEmbed.from_chapter(chapter, nsfw_allowed=nsfw_allowed)

        await ctx.send(embed=embed)

    @tasks.loop(hours=1)
    async def get_personal_feed(self) -> None:
        """Gets the current user (me)'s manga feed.
        This is all the latest released chapters in order.
        """
        order = FeedOrderQuery(created_at=Order.ascending)
        one_h_ago = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)
        feed = await self.bot.md_client.get_my_feed(
            limit=32,
            translated_language=["en", "ja"],
            order=order,
            created_at_since=one_h_ago,
            content_rating=[
                hondana.ContentRating.pornographic,
                hondana.ContentRating.safe,
                hondana.ContentRating.suggestive,
                hondana.ContentRating.erotica,
            ],
        )

        if not feed.chapters:
            return

        embeds: list[discord.Embed] = []
        for chapter in feed.chapters:
            if chapter.manga is None:
                await chapter.get_parent_manga()
            embed = await MangaDexEmbed.from_chapter(chapter, nsfw_allowed=True)
            embeds.append(embed)

        for embeds in as_chunks(embeds, 10):
            await self.webhook.send(
                embeds=embeds,
                allowed_mentions=discord.AllowedMentions(users=True),
            )

    @get_personal_feed.before_loop
    async def before_feed(self) -> None:
        await self.bot.wait_until_ready()

    @get_personal_feed.error
    async def on_loop_error(self, error: BaseException) -> None:
        import traceback

        error = getattr(error, "original", error)
        lines = traceback.format_exception(type(error), error, error.__traceback__)
        fmt = "<@!155863164544614402> \n"
        to_send = formats.to_codeblock("".join(lines), escape_md=False)

        clean = fmt + to_send
        if len(clean) >= 2000:
            password = secrets.token_urlsafe(16)
            expires = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)
            paste = await self.bot.mb_client.create_paste(
                filename="error.py", content=clean, password=password, expires=expires
            )
            clean = (
                f"Error was too long to send in a codeblock, so I have pasted it [here]({paste.url})."
                f"\nThe password is {password} and it expires at {discord.utils.format_dt(expires, 'F')}."
            )

        await self.webhook.send(clean, allowed_mentions=discord.AllowedMentions(users=True))

    def cog_unload(self) -> None:
        self.get_personal_feed.cancel()


async def setup(bot: Mipha) -> None:
    await bot.add_cog(MangaCog(bot))
