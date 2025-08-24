"""
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at http://mozilla.org/MPL/2.0/.
"""

from __future__ import annotations

import operator
import os
import re
import zlib
from io import BytesIO
from typing import TYPE_CHECKING, NamedTuple, Self

import discord
from discord import app_commands
from discord.ext import commands
from jishaku.codeblocks import Codeblock, codeblock_converter
from yarl import URL

from utilities.shared import fuzzy
from utilities.shared.formats import to_codeblock

if TYPE_CHECKING:
    from collections.abc import Generator

    from bot import Mipha
    from utilities.context import Context, Interaction
    from utilities.shared._types.pyright import PyrightResponse
    from utilities.shared._types.rtfs import RTFSResponse


RTFM_PAGE_TYPES: dict[str, str] = {
    "stable": "https://discordpy.readthedocs.io/en/stable",
    "stable-jp": "https://discordpy.readthedocs.io/ja/stable",
    "latest": "https://discordpy.readthedocs.io/en/latest",
    "latest-jp": "https://discordpy.readthedocs.io/ja/latest",
    "python": "https://docs.python.org/3",
    "python-jp": "https://docs.python.org/ja/3",
    "hondana": "https://hondana.readthedocs.io/en/stable",
    "hondana-latest": "https://hondana.readthedocs.io/en/latest",
    "twitchio": "https://twitchio.dev/en/latest/",
}

PYTHON_VER: re.Pattern[str] = re.compile(r"^3\.\d{1,2}$")


class PythonVersionConverter(commands.Converter[str]):
    async def convert(self, __: Context, argument: str) -> str:
        python_version = "3.13"

        match = PYTHON_VER.fullmatch(argument)
        if match:
            match_attempt = match[0]
            try:
                maj, _, min_ = match_attempt.partition(".")
                if 15 > int(min_) < 6:
                    return python_version
                if maj != "3":
                    return python_version
            except ValueError:
                return python_version
            else:
                python_version = match_attempt

        return python_version


class SphinxObjectFileReader:
    # Inspired by Sphinx's InventoryFileReader
    BUFSIZE = 16 * 1024

    def __init__(self, buffer: bytes) -> None:
        self.stream = BytesIO(buffer)

    def readline(self) -> str:
        return self.stream.readline().decode("utf-8")

    def skipline(self) -> None:
        self.stream.readline()

    def read_compressed_chunks(self) -> Generator[bytes, None, None]:
        decompressor = zlib.decompressobj()
        while True:
            chunk = self.stream.read(self.BUFSIZE)
            if len(chunk) == 0:
                break
            yield decompressor.decompress(chunk)
        yield decompressor.flush()

    def read_compressed_lines(self) -> Generator[str, None, None]:
        buf = b""
        for chunk in self.read_compressed_chunks():
            buf += chunk
            pos = buf.find(b"\n")
            while pos != -1:
                yield buf[:pos].decode("utf-8")
                buf = buf[pos + 1 :]
                pos = buf.find(b"\n")


def _rtfs_refresh_cooldown(interaction: Interaction) -> app_commands.Cooldown | None:
    if interaction.user.id == interaction.client.owner.id:
        return None
    return app_commands.Cooldown(1, 60)


class Libraries(discord.Enum):
    discord = "discord.py"
    hondana = "hondana"
    aiohttp = "aiohttp"
    jishaku = "jishaku"
    mystbin = "mystbin.py"
    twitchio = "twitchio"


class RTFSView(discord.ui.View):
    __slots__ = (
        "_payload",
        "owner_id",
    )

    def __init__(self, payload: RTFSResponse, /, *, lib: str, owner_id: int) -> None:
        super().__init__(timeout=60)
        self.owner_id: int = owner_id
        self._payload = payload
        options = [discord.SelectOption(label=name, value=name, description=lib) for name in payload["results"]]
        self.select_object.options = options

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Sorry, you cannot control this menu.", ephemeral=True)
            return False
        return True

    @discord.ui.select(min_values=1, max_values=1)
    async def select_object(self, interaction: Interaction, item: discord.ui.Select[Self]) -> None:
        await interaction.response.defer()
        source_item = self._payload["results"][item.values[0]]
        codeblock = to_codeblock(source_item["source"], escape_md=False)
        if len(codeblock) >= 1800:
            content = f"Sorry, the output would be too long so I'll give you the relevant URL:\n\n{source_item['url']}"
        else:
            content = f"[Relevant Source URL](<{source_item['url']}>)\n{codeblock}"

        await interaction.edit_original_response(content=content, view=self)

    @discord.ui.button(emoji="\U0001f5d1\U0000fe0f", style=discord.ButtonStyle.danger)
    async def stop_view(self, interaction: Interaction, _: discord.ui.Button[Self]) -> None:
        if interaction.message:
            await interaction.message.delete()
        self.stop()


class RTFXDetails(NamedTuple):
    raw_url: str | None
    token: str | None

    @property
    def url(self) -> URL | None:
        if self.raw_url:
            return URL(self.raw_url)

        return None


class RTFX(commands.Cog):
    _rtfm_cache: dict[str, dict[str, str]]

    def __init__(self, bot: Mipha, *, rtfs: RTFXDetails, pyright: RTFXDetails) -> None:
        self.bot = bot
        self.rtfs = rtfs
        self.pyright = pyright

    group = app_commands.Group(
        name="rtfs",
        description="Commands for 'reading the fucking source'",
        allowed_contexts=app_commands.AppCommandContext(guild=True, dm_channel=True, private_channel=True),
        allowed_installs=app_commands.AppInstallationType(guild=True, user=True),
        nsfw=False,
    )

    def parse_object_inv(self, stream: SphinxObjectFileReader, url: str) -> dict[str, str]:
        # key: URL  # noqa: ERA001
        # n.b.: key doesn't have `discord` or `discord.ext.commands` namespaces
        result: dict[str, str] = {}

        # first line is version info
        inv_version = stream.readline().rstrip()

        if inv_version != "# Sphinx inventory version 2":
            raise RuntimeError("Invalid objects.inv file version.")

        # next line is "# Project: <name>"
        # then after that is "# Version: <version>"
        projname = stream.readline().rstrip()[11:]
        stream.readline().rstrip()[11:]  # move the buffer along

        # next line says if it's a zlib header
        line = stream.readline()
        if "zlib" not in line:
            raise RuntimeError("Invalid objects.inv file, not z-lib compatible.")

        # This code mostly comes from the Sphinx repository.
        entry_regex = re.compile(r"(?x)(.+?)\s+(\S*:\S*)\s+(-?\d+)\s+(\S+)\s+(.*)")
        for line in stream.read_compressed_lines():
            match = entry_regex.match(line.rstrip())
            if not match:
                continue

            name, directive, _, location, dispname = match.groups()
            domain, _, subdirective = directive.partition(":")
            if directive == "py:module" and name in result:
                # From the Sphinx Repository:
                # due to a bug in 1.1 and below,
                # two inventory entries are created
                # for Python modules, and the first
                # one is correct
                continue

            # Most documentation pages have a label
            if directive == "std:doc":
                subdirective = "label"

            if location.endswith("$"):
                location = location[:-1] + name

            key = name if dispname == "-" else dispname
            prefix = f"{subdirective}:" if domain == "std" else ""

            if projname == "discord.py":
                key = key.replace("discord.ext.commands.", "").replace("discord.", "")

            result[f"{prefix}{key}"] = os.path.join(url, location)  # noqa: PTH118 # we're actually using this on a url for safe appending

        return result

    async def build_rtfm_lookup_table(self) -> None:
        cache: dict[str, dict[str, str]] = {}
        for key, page in RTFM_PAGE_TYPES.items():
            cache[key] = {}
            async with self.bot.session.get(page + "/objects.inv") as resp:
                if resp.status != 200:
                    raise RuntimeError("Cannot build rtfm lookup table, try again later.")

                stream = SphinxObjectFileReader(await resp.read())
                cache[key] = self.parse_object_inv(stream, page)

        self._rtfm_cache = cache

    async def do_rtfm(self, ctx: Context, key: str, obj: str | None) -> None:
        if obj is None:
            await ctx.send(RTFM_PAGE_TYPES[key])
            return None

        if not hasattr(self, "_rtfm_cache"):
            await ctx.typing()
            await self.build_rtfm_lookup_table()

        obj = re.sub(r"^(?:discord\.(?:ext\.)?)?(?:commands\.)?(.+)", r"\1", obj)

        if key.startswith("latest"):
            # point the abc.Messageable types properly:
            q = obj.lower()
            for name in dir(discord.abc.Messageable):
                if name[0] == "_":
                    continue
                if q == name:
                    obj = f"abc.Messageable.{name}"
                    break

        cache = list(self._rtfm_cache[key].items())
        matches = fuzzy.finder(obj, cache, key=operator.itemgetter(0))[:8]

        e = discord.Embed(colour=discord.Colour.blurple())
        if len(matches) == 0:
            return await ctx.send("Could not find anything. Sorry.")

        e.description = "\n".join(f"[`{key}`]({url})" for key, url in matches)
        return await ctx.send(embed=e, reference=ctx.replied_reference)

    async def rtfm_slash_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        # Degenerate case: not having built caching yet
        if not hasattr(self, "_rtfm_cache"):
            await interaction.response.autocomplete([])
            await self.build_rtfm_lookup_table()
            return []

        if not current:
            return []

        if len(current) < 3:
            return [app_commands.Choice(name=current, value=current)]

        assert interaction.command is not None
        key = interaction.command.name
        if key == "jp":
            key = "latest-jp"

        matches = fuzzy.finder(current, self._rtfm_cache[key])[:10]
        return [app_commands.Choice(name=m, value=m) for m in matches]

    @commands.hybrid_group(aliases=["rtfd"], fallback="stable")
    @app_commands.describe(entity="The object to search for")
    @app_commands.autocomplete(entity=rtfm_slash_autocomplete)
    async def rtfm(self, ctx: Context, *, entity: str | None = None) -> None:
        """Gives you a documentation link for a discord.py entity.

        Events, objects, and functions are all supported through
        a cruddy fuzzy algorithm.
        """
        await self.do_rtfm(ctx, "stable", entity)

    @rtfm.command(name="jp")
    @app_commands.describe(entity="The object to search for")
    @app_commands.autocomplete(entity=rtfm_slash_autocomplete)
    async def rtfm_jp(self, ctx: Context, *, entity: str | None = None) -> None:
        """Gives you a documentation link for a discord.py entity (Japanese)."""
        await self.do_rtfm(ctx, "latest-jp", entity)

    @rtfm.command(name="python", aliases=["py"])
    @app_commands.describe(entity="The object to search for")
    @app_commands.autocomplete(entity=rtfm_slash_autocomplete)
    async def rtfm_python(self, ctx: Context, *, entity: str | None = None) -> None:
        """Gives you a documentation link for a Python entity."""
        await self.do_rtfm(ctx, "python", entity)

    @rtfm.command(name="python-jp", aliases=["py-jp", "py-ja"])
    @app_commands.describe(entity="The object to search for")
    @app_commands.autocomplete(entity=rtfm_slash_autocomplete)
    async def rtfm_python_jp(self, ctx: Context, *, entity: str | None = None) -> None:
        """Gives you a documentation link for a Python entity (Japanese)."""
        await self.do_rtfm(ctx, "python-jp", entity)

    @rtfm.command(name="hondana")
    @app_commands.describe(entity="The object to search for")
    @app_commands.autocomplete(entity=rtfm_slash_autocomplete)
    async def rtfm_hondana(self, ctx: Context, *, entity: str | None = None) -> None:
        """Gives you a documentation link for a Hondana entity."""
        await self.do_rtfm(ctx, "hondana", entity)

    @rtfm.command(name="hondana-latest")
    @app_commands.describe(entity="The object to search for")
    @app_commands.autocomplete(entity=rtfm_slash_autocomplete)
    async def rtfm_hondana_latest(self, ctx: Context, *, entity: str | None = None) -> None:
        """Gives you a documentation link for a Hondana entity."""
        await self.do_rtfm(ctx, "hondana-latest", entity)

    @rtfm.command(name="twitchio", aliases=["tio"])
    @app_commands.describe()
    @app_commands.autocomplete(entity=rtfm_slash_autocomplete)
    async def rtfm_twitchio(self, ctx: Context, *, entity: str | None = None) -> None:
        """Gives you a documentation link for a TwitchIO entity."""
        await self.do_rtfm(ctx, "twitchio", entity)

    @rtfm.command(name="refresh", with_app_command=False)
    @commands.is_owner()
    async def rtfm_refresh(self, ctx: Context) -> None:
        """Refreshes the RTFM and FAQ cache"""

        async with ctx.typing():
            await self.build_rtfm_lookup_table()

        await ctx.send("\N{THUMBS UP SIGN}")

    async def _get_rtfs(self, *, library: Libraries, search: str, exact: bool) -> RTFSResponse:
        if not self.rtfs.url:
            raise ValueError("RTFS details not configured correctly")

        headers = {"Authorization": self.rtfs.token} if self.rtfs.token else None
        async with self.bot.session.get(
            self.rtfs.url,
            params={"format": "source", "library": library.value, "search": search, "direct": "true" if exact else "false"},
            headers=headers,
        ) as resp:
            return await resp.json()

    async def _update_rtfs(self) -> bool:
        if not self.rtfs.token or not self.rtfs.url:
            return False

        async with self.bot.session.post(self.rtfs.url / "refresh", headers={"Authorization": self.rtfs.token}) as resp:
            data = await resp.json()

        return data["success"]

    def _parse_pyright_output(self, data: PyrightResponse) -> str:
        counts = {"error": 0, "warn": 0, "info": 0}

        diagnostics = []
        for diagnostic in data["result"]["generalDiagnostics"]:
            start = diagnostic["range"]["start"]
            start = f"{start['line']}:{start['character']}"

            severity = diagnostic["severity"]
            if severity != "error":
                severity = severity[:4]
            counts[severity] += 1

            prefix = " " if severity == "info" else "-"
            message = diagnostic["message"].replace("\n", f"\n{prefix} ")

            diagnostics.append(f"{prefix} {start} - {severity}: {message}")

        pyr_version = data["pyright_version"]
        py_version = data["python_version"]
        node_version = data["node_version"]
        executed_version = data.get("executed_python_version", "3.13")
        diagnostics = "\n".join(diagnostics)
        totals = ", ".join(f"{count} {name}" for name, count in counts.items())

        return to_codeblock(
            (
                f"Pyright v{pyr_version} | Python v{py_version} | Executed Python v{executed_version} "
                f"| Node {node_version}:\n\n{diagnostics}\n\n{totals}\n"
            ),
            language="diff",
            escape_md=False,
        )

    @group.command(name="search")
    @app_commands.describe(
        library="Which library to search the source for.",
        search="Your search query.",
        exact="If you want to access the item by the exact name you're passing.",
        ephemeral="If you want this command execution to be private.",
    )
    async def rtfs_callback(
        self,
        interaction: Interaction,
        library: Libraries,
        search: str,
        exact: bool = False,  # noqa: FBT001, FBT002 # required for slash parameters
        ephemeral: bool = False,  # noqa: FBT001, FBT002 # required for slash parameters
    ) -> None:
        """RTFM command for loading source code/searching from libraries."""
        rtfs = await self._get_rtfs(library=library, search=search, exact=exact)
        if not rtfs["results"]:
            await interaction.response.send_message("Sorry, that search returned no results.", ephemeral=True)
            return

        view = RTFSView(rtfs, lib=library.value, owner_id=interaction.user.id)
        await interaction.response.send_message(view=view, ephemeral=ephemeral)

    @group.command(name="refresh")
    @app_commands.checks.dynamic_cooldown(_rtfs_refresh_cooldown)
    async def rtfs_refresh(self, interaction: Interaction) -> None:
        """Schedules an update of the RTFS library code in the API."""
        await interaction.response.defer(ephemeral=True)

        success = await self._update_rtfs()
        content = "Okay, all done!" if success else f"Sorry, something broke here. Ask <@{self.bot.owner.id}> about it."

        return await interaction.followup.send(content, allowed_mentions=discord.AllowedMentions.none())

    @rtfs_refresh.error
    async def refresh_error(self, interaction: Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.CommandOnCooldown):
            await interaction.response.send_message(
                "Sorry, this has already been requested recently. "
                f"Please wait at least {error.retry_after:.2f}s before trying again.",
            )
            return

        return

    async def _perform_pyright(self, code: str, /, *, python_version: str) -> PyrightResponse:
        if not self.pyright.url:
            raise ValueError("Sorry, this feature has not been configured.")

        async with self.bot.session.post(
            self.pyright.url,
            headers={"Authorization": self.pyright.token} if self.pyright.token else None,
            json={"content": code, "version": python_version},
        ) as resp:
            return await resp.json()

    @commands.command(name="pyright", aliases=["pr"])
    async def _pyright(
        self,
        ctx: Context,
        python_version: str = commands.param(converter=PythonVersionConverter, default="3.13"),
        *,
        codeblock: Codeblock = commands.param(converter=codeblock_converter),  # noqa: B008 # this is how commands.param works
    ) -> None:
        """
        Evaluates Python code through the latest (installed) version of Pyright on my system.
        """
        code = codeblock.content

        try:
            output: PyrightResponse = await self._perform_pyright(code, python_version=python_version)
        except ValueError:
            return await ctx.send("Sorry, this functionality is currently disabled.")

        fmt = self._parse_pyright_output(output)

        return await ctx.send(fmt)


async def setup(bot: Mipha) -> None:
    rtfs_config = bot.config.get("rtfs", {})
    pyright_config = bot.config.get("pyright", {})
    rtfs = RTFXDetails(rtfs_config.get("url"), rtfs_config.get("token"))
    pyright = RTFXDetails(pyright_config.get("url"), pyright_config.get("token"))
    await bot.add_cog(RTFX(bot, rtfs=rtfs, pyright=pyright))
