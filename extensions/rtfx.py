"""
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at http://mozilla.org/MPL/2.0/.
"""

from __future__ import annotations

import os
import pathlib
from typing import TYPE_CHECKING, Self

import discord
from discord import app_commands
from discord.ext import commands
from jishaku.codeblocks import Codeblock, codeblock_converter
from jishaku.shell import ShellReader

from utilities.shared.formats import from_json, to_codeblock, to_json

if TYPE_CHECKING:
    from bot import Mipha
    from utilities.context import Context, Interaction
    from utilities.shared._types.rtfs import RTFSResponse

RTFS_URL = "https://rtfs.abstractumbra.dev"


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
        options = [discord.SelectOption(label=name, value=name, description=lib) for name in payload["nodes"]]
        self.select_object.options = options

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Sorry, you cannot control this menu.", ephemeral=True)
            return False
        return True

    @discord.ui.select(min_values=1, max_values=1)
    async def select_object(self, interaction: Interaction, item: discord.ui.Select[Self]) -> None:
        await interaction.response.defer()
        source_item = self._payload["nodes"][item.values[0]]
        codeblock = to_codeblock(source_item["source"], escape_md=False)
        if len(codeblock) >= 2000:
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

    async def _get_rtfs(self, *, library: Libraries, search: str) -> RTFSResponse:
        headers = {"Authorization": self.rtfs_token} if self.rtfs_token else None
        async with self.bot.session.get(
            RTFS_URL, params={"format": "source", "library": library.value, "search": search}, headers=headers
        ) as resp:
            return await resp.json()

    async def _update_rtfs(self) -> bool:
        if not self.rtfs_token:
            return False

        async with self.bot.session.post(RTFS_URL + "/refresh", headers={"Authorization": self.rtfs_token}) as resp:
            data = await resp.json()

        return data["success"]

    @group.command(name="search")
    @app_commands.describe(library="Which library to search the source for.", search="Your search query.")
    async def rtfs_callback(
        self, interaction: Interaction, library: Libraries, search: str, ephemeral: bool = False
    ) -> None:
        """RTFM command for loading source code/searching from libraries."""
        rtfs = await self._get_rtfs(library=library, search=search)
        if not rtfs["nodes"]:
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
                f"Sorry, this has already been requested recently. Please wait at least {round(error.retry_after, 2)}s before trying again."
            )

    @commands.command(name="rtfs")
    async def rtfs_prefix(self, ctx: Context, *args: str) -> None:
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
