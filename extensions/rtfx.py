"""
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at http://mozilla.org/MPL/2.0/.
"""

from __future__ import annotations

import inspect
import operator
import os
import pathlib
import sys
from textwrap import dedent
from typing import TYPE_CHECKING, Any

import asyncpg  # type: ignore # rtfs
import discord
import hondana  # type: ignore # rtfs
import jishaku  # type: ignore # rtfs
from discord import app_commands, ui  # type: ignore # rtfs
from discord.ext import commands, menus, tasks  # type: ignore # rtfs
from jishaku.codeblocks import Codeblock, codeblock_converter
from jishaku.shell import ShellReader

from utilities.shared.formats import from_json, to_codeblock, to_json

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

    from bot import Mipha
    from utilities.context import Context

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
)


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
        elif top_level in ("commands", "tasks"):
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

            recur = inspect.getattr_static(recur, item, None) if recur else inspect.getattr_static(module, item, None)
            current += f".{item}"

            if recur is None:
                raise BadSource(f"{current} is not a valid module path.")

        if isinstance(recur, property):
            recur = recur.fget
        elif inspect.ismemberdescriptor(recur):
            raise BadSource(f"`{current}` seems like it's an instance attribute, can't source those")

        if isinstance(recur, operator.attrgetter):
            prop = argument.rsplit(".")[-1]
            return await self.convert(ctx, f"discord.User.{prop}")
        # ctx.bot.log_handler.log.info("Recur is %s (type %s)", recur, type(recur))
        return inspect.getsource(recur)  # type: ignore # unreachable


class RTFX(commands.Cog):
    def __init__(self, bot: Mipha) -> None:
        self.bot = bot

    @commands.command(name="rtfs")
    async def rtfs(
        self,
        ctx: Context,
        *,
        target: str | None = commands.param(converter=SourceConverter, default=None),
    ) -> None:
        """
        This command will provide the source code of a given entity.
        If called without an argument it will show all possible sources.

        Note that special dunders and whatnot are not sourceable, nor are any Python objects implemented in C.
        """
        if target is None:
            await ctx.send(embed=discord.Embed(title="Available sources of rtfs", description="\n".join(RTFS)))
            return

        new_target = dedent(target)

        if len(new_target) < 2000:
            new_target = to_codeblock(new_target, language="py", escape_md=False)

        await ctx.send(new_target, paste_language="py")

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

        await ctx.typing()
        rand = os.urandom(16).hex()
        with_file = pyright_dump / f"{rand}_tmp_pyright.py"
        with_file.touch(mode=0o0777, exist_ok=True)

        with with_file.open("w") as f:
            f.write(code)

        output: str = ""
        with ShellReader(f"cd _pyright && pyright --outputjson {with_file.name}") as reader:
            async for line in reader:
                if not line.startswith("[stderr] "):
                    output += line

        with_file.unlink(missing_ok=True)

        counts = {"error": 0, "warn": 0, "info": 0}

        data = from_json(output)

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


async def setup(bot: Mipha) -> None:
    await bot.add_cog(RTFX(bot))
