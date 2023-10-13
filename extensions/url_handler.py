from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from discord import app_commands, ui
from discord.ext import commands, tasks
from yarl import URL

from utilities.markdown import MarkdownBuilder
from utilities.ui import MiphaBaseView

if TYPE_CHECKING:
    from typing_extensions import Self

    from bot import Mipha
    from utilities.context import Context, Interaction

LOGGER = logging.getLogger(__name__)
AD_LISTS: list[str] = [
    "https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts",
    "http://sysctl.org/cameleon/hosts",
    "https://s3.amazonaws.com/lists.disconnect.me/simple_tracking.txt",
    "https://s3.amazonaws.com/lists.disconnect.me/simple_ad.txt",
    "https://adaway.org/hosts.txt",
]


class URLConverter(commands.Converter[URL]):
    async def convert(self, ctx: Context, input_: str) -> URL:
        input_ = input_.removeprefix("<").removesuffix(">")
        try:
            url = URL(input_)
        except (ValueError, TypeError):
            await ctx.send("Not a valid URL.")
            raise

        return url


class URLUnfurlView(MiphaBaseView):
    __slots__ = (
        "url",
        "bot",
        "_redirect_url",
        "markdown_handler",
    )

    def __init__(self, url: URL, /, *, timeout: float | None = 30, bot: Mipha) -> None:
        self.url: URL = url
        self.bot: Mipha = bot
        self._redirect_url: URL | None = None
        self.markdown_handler = MarkdownBuilder()
        super().__init__(timeout=timeout)

    def _post_init(self) -> None:
        if self.url.fragment or (self._redirect_url and self._redirect_url.fragment):
            self.fragment_button.disabled = False
        else:
            self.fragment_button.disabled = True

        if self.url.query or (self._redirect_url and self._redirect_url.query):
            self.query_param_button.disabled = False
        else:
            self.query_param_button.disabled = True

    async def _resolve_redirect(self) -> None:
        async with self.bot.session.get(
            self.url,
            allow_redirects=False,
            headers={
                "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/113.0",
                "accept": "*/*",
            },
        ) as response:
            if location := response.headers.get("Location"):
                self._redirect_url = URL(location)
                self.redirect_button.disabled = False
        self._post_init()

    @ui.button(label="Query parameters", emoji="\U0001f4f0")
    async def query_param_button(self, interaction: Interaction, button: ui.Button[Self]) -> None:
        await interaction.response.defer(ephemeral=True)

        if self.url.query_string:
            self.markdown_handler.add_header(text="URL Query Parameters")
            # self.markdown_handler.add_text(text=f"`{self.url.query_string}`")
            self.markdown_handler.add_bulletpoints(texts=[f"{key} -> {value}" for key, value in self.url.query.items()])
        if self._redirect_url and self._redirect_url.query_string:
            self.markdown_handler.add_header(text="Redirect URL Query Parameters")
            # self.markdown_handler.add_text(text=f"`{self._redirect_url.query_string}`")
            self.markdown_handler.add_bulletpoints(
                texts=[f"{key} -> `{value}`" for key, value in self._redirect_url.query.items()]
            )

        await interaction.followup.send(self.markdown_handler.text, ephemeral=True)

    @ui.button(label="Fragments", emoji="\U0001f9e9")
    async def fragment_button(self, interaction: Interaction, button: ui.Button[Self]) -> None:
        await interaction.response.defer(ephemeral=True)

        if self.url.fragment:
            self.markdown_handler.add_header(text="URL Fragments")
            self.markdown_handler.add_text(text=f"{self.url.fragment}")
        if self._redirect_url and self._redirect_url.fragment:
            self.markdown_handler.add_header(text="Redirect URL Fragments")
            self.markdown_handler.add_text(text=f"{self._redirect_url.fragment}")

        await interaction.followup.send(self.markdown_handler.text, ephemeral=True)

    @ui.button(label="Redirect", emoji="\U000021aa\U0000fe0f", disabled=True)
    async def redirect_button(self, interaction: Interaction, button: ui.Button[Self]) -> None:
        assert self._redirect_url is not None
        await interaction.response.defer(ephemeral=True)

        self.markdown_handler.add_header(text="Redirect found")
        self.markdown_handler.add_text(text=f"|| <{self._redirect_url}> ||")
        self.markdown_handler.add_newline()
        self.markdown_handler.add_link(url=self._redirect_url, text="URL Link")

        await interaction.followup.send(self.markdown_handler.text, ephemeral=True)


class URLChecker(commands.Cog):
    def __init__(self, bot: Mipha, /) -> None:
        self.bot: Mipha = bot
        self.ad_lists: dict[str, list[str]] = {
            "StevenBlack": [],
            "Sysctl": [],
            "DisconnectTracking": [],
            "DisconnectAD": [],
            "AdAway": [],
        }
        self.update_adlist_master.start()

    async def cog_unload(self) -> None:
        self.update_adlist_master.cancel()

    @commands.hybrid_command(name="check-url", aliases=["url"])
    @app_commands.describe(url="The URL to check against.")
    async def url_checker(
        self, ctx: Context, *, url: URL = commands.param(converter=URLConverter, description="The URL to check against.")
    ) -> None:
        """Checks a URL against known adlists and for redirects, query parameters and fragments."""
        if ctx.guild and ctx.channel.permissions_for(ctx.me).manage_messages:  # type: ignore # guarded
            await ctx.message.edit(suppress=True)

        url_found_in: list[str] = []
        for key, value in self.ad_lists.items():
            if url.host in value:
                url_found_in.append(key)

        async with ctx.typing(ephemeral=True):
            view = URLUnfurlView(url, bot=ctx.bot)
            await view._resolve_redirect()
            LOGGER.info(
                "Reversing URL %r\nHost: %r\nPath: %r\nQuery strings: %r\nFragments: %r\nRedirect URL: %r",
                url.human_repr(),
                url.host,
                url.path,
                url.query_string,
                url.fragment,
                (view._redirect_url and view._redirect_url.human_repr()) or "N/A",
            )

            fmt = ""
            if url_found_in:
                fmt += "URL was found in the following ad or tracker lists:-\n\n"
                fmt += "\n".join(url_found_in)

            view.message = await ctx.send(f"URL Details follow:-{fmt}", view=view, ephemeral=True, wait=True)

    @tasks.loop(hours=24)
    async def update_adlist_master(self) -> None:
        for url, key in zip(AD_LISTS, self.ad_lists.keys()):
            LOGGER.info("Updating adlist '%s' ('%s').", key, url)
            async with self.bot.session.get(url) as resp:
                data = await resp.text()
                clean_lines = data.splitlines()
                clean_lines = [line for line in clean_lines if (line and not line.startswith("#"))]
                self.ad_lists[key] = clean_lines

    @update_adlist_master.before_loop
    async def before_adlist_update(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: Mipha) -> None:
    await bot.add_cog(URLChecker(bot))
