"""
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at http://mozilla.org/MPL/2.0/.
"""

from __future__ import annotations

import inspect
import io
import json
import os
import pathlib
import re
import sys
import zlib
from textwrap import dedent
from types import ModuleType
from typing import TYPE_CHECKING, Any, Callable, Generator

import asyncpg  # type: ignore # rtfs
import discord
import hondana  # type: ignore # rtfs
import jishaku  # type: ignore # rtfs
import mystbin  # type: ignore # rtfs
from discord import app_commands, ui  # type: ignore # rtfs
from discord.ext import commands, menus, tasks  # type: ignore # rtfs
from jishaku.codeblocks import Codeblock, codeblock_converter
from jishaku.shell import ShellReader

from utilities import fuzzy
from utilities.context import Context
from utilities.formats import to_codeblock


if TYPE_CHECKING:
    from bot import Mipha

RTFS = (
    "discord",
    "discord.ext.commands",
    "commands",
    "discord.app_commands",
    "app_commands",
    "discord.ext.tasks",
    "tasks",
    "discord.ext.menus",
    "menus",
    "discord.ui",
    "ui",
    "asyncpg",
    "hondana",
    "mystbin",
)

RTFM_PAGE_TYPES = {
    "discord.py": "https://discordpy.readthedocs.io/en/stable",
    "discord.py-master": "https://discordpy.readthedocs.io/en/latest",
    "python": "https://docs.python.org/3",
    "python-jp": "https://docs.python.org/ja/3",
    "asyncpg": "https://magicstack.github.io/asyncpg/current",
    "aiohttp": "https://docs.aiohttp.org/en/stable",
    "hondana": "https://hondana.readthedocs.io/en/stable",
    "hondana-master": "https://hondana.readthedocs.io/en/latest",
}


class BadSource(commands.CommandError):
    pass


class SourceConverter(commands.Converter[str]):
    async def convert(self, ctx: Context, argument: str) -> str | None:
        args = argument.split(".")
        top_level = args.pop(0)
        if top_level in (
            "app_commands",
            "ui",
        ):
            top_level = f"discord.{top_level}"
        elif top_level in ("commands",):
            top_level = f"discord.ext.{top_level}"

        if top_level not in RTFS:
            raise BadSource(f"`{top_level}` is not an allowed sourceable module.")

        module = sys.modules[top_level]

        if not args:
            return inspect.getsource(module)

        current = top_level

        recur: ModuleType | Callable[[Any], Any] | property | None = None

        for item in args:
            if item == "":
                raise BadSource("Don't even try.")

            if recur:
                recur = inspect.getattr_static(recur, item, None)
            else:
                recur = inspect.getattr_static(module, item, None)
            current += f".{item}"

            if recur is None:
                raise BadSource(f"{current} is not a valid module path.")

        if isinstance(recur, property):
            recur = recur.fget

        return inspect.getsource(recur)  # type: ignore # unreachable


class SphinxObjectFileReader:
    """A Sphinx file reader."""

    # Inspired by Sphinx's InventoryFileReader
    BUFSIZE = 16 * 1024

    def __init__(self, buffer: bytes) -> None:
        self.stream = io.BytesIO(buffer)

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


class RTFX(commands.Cog):
    def __init__(self, bot: Mipha) -> None:
        self.bot = bot

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
        _ = stream.readline().rstrip()[11:]

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
            elif projname == "asyncpg":
                key = key.replace("asyncpg.", "")
            elif projname == "Hondana":
                key = key.replace("hondana.", "")

            result[f"{prefix}{key}"] = os.path.join(url, location)

        return result

    async def build_rtfm_lookup_table(self) -> None:
        cache = {}
        for key, page in RTFM_PAGE_TYPES.items():
            _ = cache[key] = {}
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

        obj = re.sub(r"^(?:(?:discord\.(?:ext\.)?)?(?:commands\.)|hondana\.)?(.+)", r"\1", obj)

        if key.startswith("discord."):
            # point the abc.Messageable types properly:
            q = obj.lower()
            for name in dir(discord.abc.Messageable):
                if name[0] == "_":
                    continue
                if q == name:
                    obj = f"abc.Messageable.{name}"
                    break

        cache = list(self._rtfm_cache[key].items())

        matches = fuzzy.finder(obj, cache, key=lambda t: t[0], raw=False)

        e = discord.Embed(colour=discord.Colour.dark_magenta())
        if not matches:
            await ctx.send("Could not find anything. Sorry.")
            return
        e.title = f"RTFM for __**`{key}`**__: {obj}"
        e.description = "\n".join(f"[`{key}`]({url})" for key, url in matches[:8])
        e.set_footer(text=f"{len(matches)} possible results.")
        await ctx.send(embed=e)

    @commands.group(aliases=["rtfd"], invoke_without_command=True)
    async def rtfm(self, ctx: Context, *, obj: str | None = None) -> None:
        """Gives you a documentation link for a python project entity. Defaults to providing information on discord.py

        Events, objects, and functions are all supported through a
        a cruddy fuzzy algorithm.
        """
        if isinstance(ctx.channel, discord.TextChannel) and ctx.channel.category_id == 929884995789668362:
            await self.do_rtfm(ctx, "hondana", obj)
            return
        await self.do_rtfm(ctx, "discord.py", obj)

    @rtfm.command(name="master", aliases=["dpym"])
    async def rtfm_dpy_master(self, ctx: Context, *, obj: str | None = None) -> None:
        """Gives you a documentation link for a discord.py entity targetting the master branch."""
        await self.do_rtfm(ctx, "discord.py-master", obj)

    @rtfm.command(name="python", aliases=["py"])
    async def rtfm_python(self, ctx: Context, *, obj: str | None = None) -> None:
        """Gives you a documentation link for a Python entity."""
        await self.do_rtfm(ctx, "python", obj)

    @rtfm.command(name="py-jp", aliases=["py-ja"])
    async def rtfm_python_jp(self, ctx: Context, *, obj: str | None = None) -> None:
        """Gives you a documentation link for a Python entity (Japanese)."""
        await self.do_rtfm(ctx, "python-jp", obj)

    @rtfm.command(name="asyncpg")
    async def rtfm_asyncpg(self, ctx: Context, *, obj: str | None = None) -> None:
        """Gives you the documentation link for an `asyncpg` entity."""
        await self.do_rtfm(ctx, "asyncpg", obj)

    @rtfm.command(name="aiohttp")
    async def rtfm_aiohttp(self, ctx: Context, *, obj: str | None = None) -> None:
        """Gives you the documentation link for an `aiohttp` entity."""
        await self.do_rtfm(ctx, "aiohttp", obj)

    @rtfm.command(name="hondana")
    async def rtfm_hondana(self, ctx: Context, *, obj: str | None = None) -> None:
        """Gives you the documentation link for a `Hondana` entity."""
        await self.do_rtfm(ctx, "hondana", obj)

    @rtfm.command(name="hondana-m")
    async def rtfm_hondana_master(self, ctx: Context, *, obj: str | None = None) -> None:
        """Gives you the documentation link for a `Hondana` entity targetting the master branch."""
        await self.do_rtfm(ctx, "hondana-master", obj)

    @rtfm.command(name="refresh")
    @commands.is_owner()
    async def rtfm_refresh(self, ctx: Context) -> None:
        """Refreshes the RTFM and FAQ cache"""

        async with ctx.typing():
            await self.build_rtfm_lookup_table()

        await ctx.send("\N{THUMBS UP SIGN}")

    @commands.command(name="rtfs")
    async def rtfs(
        self, ctx: Context, *, target: str | None = commands.param(converter=SourceConverter, default=None)
    ) -> None:
        if target is None:
            await ctx.send(embed=discord.Embed(title="Available sources of rtfs", description="\n".join(RTFS)))
            return

        new_target = dedent(target)

        if len(new_target) < 2000:
            new_target = to_codeblock(new_target, language="py", escape_md=False)

        await ctx.send(new_target, mystbin_syntax="py")

    @rtfs.error
    async def rtfs_error(self, ctx: Context, error: commands.CommandError) -> None:
        error = getattr(error, "original", error)

        if isinstance(error, (TypeError, BadSource)):
            await ctx.send(f"Not a valid source-able type or path:-\n\n{error}.")

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

        pyright_dump = pathlib.Path("./_pyright/")
        if not pyright_dump.exists():
            pyright_dump.mkdir(mode=0o0755, parents=True, exist_ok=True)
            conf = pyright_dump / "pyrightconfig.json"
            conf.touch()
            with open(conf, "w") as f:
                f.write(
                    json.dumps(
                        {
                            "pythonVersion": "3.10",
                            "typeCheckingMode": "basic",
                            "useLibraryCodeForTypes": False,
                            "reportMissingImports": True,
                        }
                    )
                )

        await ctx.typing()
        rand = os.urandom(16).hex()
        with_file = pyright_dump / f"{rand}_tmp_pyright.py"
        with_file.touch(mode=0o0777, exist_ok=True)

        with open(with_file, "w") as f:
            f.write(code)

        output: str = ""
        with ShellReader(f"cd _pyright && pyright --outputjson {with_file.name}") as reader:
            async for line in reader:
                if not line.startswith("[stderr] "):
                    output += line

        with_file.unlink(missing_ok=True)

        counts = {"error": 0, "warn": 0, "info": 0}

        data = json.loads(output)

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

        fmt = to_codeblock(f"Pyright v{version}:\n\n{diagnostics}\n\n{totals}\n", language="diff", escape_md=False)
        await ctx.send(fmt)


async def setup(bot) -> None:
    await bot.add_cog(RTFX(bot))
