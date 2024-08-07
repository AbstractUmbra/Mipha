"""
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at http://mozilla.org/MPL/2.0/.
"""

from __future__ import annotations

import os
import pathlib
import re
import zlib
from io import BytesIO
from typing import TYPE_CHECKING, Any, Self

import discord
from discord import app_commands
from discord.ext import commands
from jishaku.codeblocks import Codeblock, codeblock_converter
from jishaku.shell import ShellReader

from utilities.shared import fuzzy
from utilities.shared.formats import from_json, to_codeblock, to_json

if TYPE_CHECKING:
    from collections.abc import Generator

    from bot import Mipha
    from utilities.context import Context, Interaction
    from utilities.shared._types.rtfs import RTFSResponse

RTFS_URL: str = "https://rtfs.abstractumbra.dev"


RTFM_PAGE_TYPES: dict[str, str] = {
    "stable": "https://discordpy.readthedocs.io/en/stable",
    "stable-jp": "https://discordpy.readthedocs.io/ja/stable",
    "latest": "https://discordpy.readthedocs.io/en/latest",
    "latest-jp": "https://discordpy.readthedocs.io/ja/latest",
    "python": "https://docs.python.org/3",
    "python-jp": "https://docs.python.org/ja/3",
    "hondana": "https://hondana.readthedocs.io/en/stable",
    "hondana-latest": "https://hondana.readthedocs.io/en/latest",
}


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


def _rtfs_cooldown(interaction: Interaction) -> app_commands.Cooldown | None:
    if interaction.user.id == interaction.client.owner.id:
        return None
    return app_commands.Cooldown(1, 60)


class Libraries(discord.Enum):
    discord = "discord.py"
    hondana = "hondana"
    aiohttp = "aiohttp"
    jishaku = "jishaku"
    wavelink = "wavelink"
    mystbin = "mystbin.py"


class RTFSView(discord.ui.View):
    __slots__ = (
        "owner_id",
        "_payload",
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
    async def stop_view(self, interaction: Interaction, button: discord.ui.Button[Self]) -> None:
        if interaction.message:
            await interaction.message.delete()
        self.stop()


class RTFX(commands.Cog):
    _rtfm_cache: dict[str, dict[str, str]]

    def __init__(self, bot: Mipha) -> None:
        self.bot = bot
        self.rtfs_token: str | None = self.bot.config.get("rtfs", {}).get("token")

    group = app_commands.Group(
        name="rtfs",
        description="Commands for 'reading the fucking source'",
        allowed_contexts=app_commands.AppCommandContext(guild=True, dm_channel=True, private_channel=True),
        allowed_installs=app_commands.AppInstallationType(guild=True, user=True),
        nsfw=False,
    )

    def parse_object_inv(self, stream: SphinxObjectFileReader, url: str) -> dict[str, str]:
        # key: URL
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
            return

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
        matches = fuzzy.finder(obj, cache, key=lambda t: t[0])[:8]

        e = discord.Embed(colour=discord.Colour.blurple())
        if len(matches) == 0:
            return await ctx.send("Could not find anything. Sorry.")

        e.description = "\n".join(f"[`{key}`]({url})" for key, url in matches)
        await ctx.send(embed=e, reference=ctx.replied_reference)

    async def rtfm_slash_autocomplete(
        self, interaction: discord.Interaction, current: str
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

    @rtfm.command(name="refresh", with_app_command=False)
    @commands.is_owner()
    async def rtfm_refresh(self, ctx: Context) -> None:
        """Refreshes the RTFM and FAQ cache"""

        async with ctx.typing():
            await self.build_rtfm_lookup_table()

        await ctx.send("\N{THUMBS UP SIGN}")

    async def _get_rtfs(self, *, library: Libraries, search: str, exact: bool) -> RTFSResponse:
        headers = {"Authorization": self.rtfs_token} if self.rtfs_token else None
        async with self.bot.session.get(
            RTFS_URL,
            params={"format": "source", "library": library.value, "search": search, "direct": "true" if exact else "false"},
            headers=headers,
        ) as resp:
            return await resp.json()

    async def _update_rtfs(self) -> bool:
        if not self.rtfs_token:
            return False

        async with self.bot.session.post(RTFS_URL + "/refresh", headers={"Authorization": self.rtfs_token}) as resp:
            data = await resp.json()

        return data["success"]

    def _setup_pyright(self) -> pathlib.Path:
        pyright_dump = pathlib.Path("./_pyright/")
        if not pyright_dump.exists():
            pyright_dump.mkdir(mode=0o0755, parents=True, exist_ok=True)
            conf = pyright_dump / "pyrightconfig.json"
            conf.touch()
            with conf.open("w") as f:
                f.write(
                    to_json(
                        {
                            "pythonVersion": "3.12",
                            "typeCheckingMode": "strict",
                            "useLibraryCodeForTypes": False,
                            "reportMissingImports": True,
                        },
                    ),
                )

        return pyright_dump

    def _parse_pyright_output(self, data: dict[str, Any]) -> str:
        counts = {"error": 0, "warn": 0, "info": 0}

        diagnostics = []
        for diagnostic in data["generalDiagnostics"]:
            start = diagnostic["range"]["start"]
            start = f"{start['line']}:{start['character']}"

            severity = diagnostic["severity"]
            if severity != "error":
                severity = severity[:4]
            counts[severity] += 1

            prefix = " " if severity == "info" else "-"
            message = diagnostic["message"].replace("\n", f"\n{prefix} ")

            diagnostics.append(f"{prefix} {start} - {severity}: {message}")

        version = data["version"]
        diagnostics = "\n".join(diagnostics)
        totals = ", ".join(f"{count} {name}" for name, count in counts.items())

        return to_codeblock(f"Pyright v{version}:\n\n{diagnostics}\n\n{totals}\n", language="diff", escape_md=False)

    @group.command(name="search")
    @app_commands.describe(
        library="Which library to search the source for.",
        search="Your search query.",
        exact="If you want to access the item by the exact name you're passing.",
        ephemeral="If you want this command execution to be private.",
    )
    async def rtfs_callback(
        self, interaction: Interaction, library: Libraries, search: str, exact: bool = False, ephemeral: bool = False
    ) -> None:
        """RTFM command for loading source code/searching from libraries."""
        rtfs = await self._get_rtfs(library=library, search=search, exact=exact)
        if not rtfs["results"]:
            return await interaction.response.send_message("Sorry, that search returned no results.", ephemeral=True)

        view = RTFSView(rtfs, lib=library.value, owner_id=interaction.user.id)
        await interaction.response.send_message(view=view, ephemeral=ephemeral)

    @group.command(name="refresh")
    @app_commands.checks.dynamic_cooldown(_rtfs_cooldown)
    async def rtfs_refresh(self, interaction: Interaction) -> None:
        """Schedules an update of the RTFS library code in the API."""
        await interaction.response.defer(ephemeral=True)

        success = await self._update_rtfs()
        content = "Okay, all done!" if success else f"Sorry, something broke here. Ask <@{self.bot.owner.id}> about it."

        return await interaction.followup.send(content, allowed_mentions=discord.AllowedMentions.none())

    @rtfs_refresh.error
    async def refresh_error(self, interaction: Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.CommandOnCooldown):
            return await interaction.response.send_message(
                f"Sorry, this has already been requested recently. Please wait at least {error.retry_after:.2f}s before trying again."
            )

    @commands.command(name="rtfs", ignore_extra=True)
    async def rtfs_prefix(self, ctx: Context) -> None:
        mention = "/rtfs search"
        app_group = ctx.bot.tree.get_command("rtfs", type=discord.AppCommandType.chat_input)
        if app_group and isinstance(app_group, app_commands.Group):
            app_command = app_group.get_command("search")
            if app_command:
                mention = await ctx.bot.tree.find_mention_for(app_command)
        return await ctx.send(f"Migrated to a slash command, sorry. Use {mention}")

    @commands.command(name="pyright", aliases=["pr"])
    async def _pyright(
        self,
        ctx: Context,
        *,
        codeblock: Codeblock = commands.param(converter=codeblock_converter),
    ) -> None:
        """
        Evaluates Python code through the latest (installed) version of Pyright on my system.
        """
        code = codeblock.content

        path = self._setup_pyright()

        await ctx.typing()
        rand = os.urandom(16).hex()
        with_file = path / f"{rand}_tmp_pyright.py"
        with_file.touch(mode=0o0777, exist_ok=True)

        with with_file.open("w") as f:
            f.write(code)

        output: str = ""
        with ShellReader(f"cd _pyright && pyright --outputjson {with_file.name}") as reader:
            async for line in reader:
                if not line.startswith("[stderr] "):
                    output += line

        with_file.unlink(missing_ok=True)

        data = from_json(output)

        fmt = self._parse_pyright_output(data)

        await ctx.send(fmt)


async def setup(bot: Mipha) -> None:
    await bot.add_cog(RTFX(bot))
