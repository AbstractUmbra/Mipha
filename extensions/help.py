"""
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at http://mozilla.org/MPL/2.0/.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from utilities.shared.paginator import RoboPages, SimpleListSource

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Mapping

    from bot import Mipha
    from utilities.context import Context


class PaginatedHelpCommand(commands.HelpCommand):
    context: Context

    def __init__(self, *, verify_checks: bool = True, show_hidden: bool = False) -> None:
        super().__init__(verify_checks=verify_checks, show_hidden=show_hidden)

    async def recursive_command_format(
        self,
        command: commands.Command,
        *,
        indent: int = 1,
        subc: int = 0,
    ) -> AsyncGenerator[str, None]:
        yield ("" if indent == 1 else "├" if subc != 0 else "└") + f"`{command.qualified_name}`: {command.short_doc}"
        if isinstance(command, commands.Group):
            last = len(command.commands) - 1
            for _, command in enumerate(await self.filter_commands(command.commands, sort=True)):
                async for result in self.recursive_command_format(command, indent=indent + 1, subc=last):
                    yield result
                last -= 1

    async def format_commands(
        self,
        cog: commands.Cog,
        cmds: list[commands.Group | commands.Command],
        *,
        pages: list[discord.Embed],
    ) -> None:
        if not cmds:
            return

        pg = commands.Paginator(max_size=2000, prefix="", suffix="")

        for command in cmds:
            try:
                await command.can_run(self.context)
            except (discord.Forbidden, commands.CheckFailure, commands.CommandError):
                continue
            else:
                async for line in self.recursive_command_format(command):
                    pg.add_line(line)

        for desc in pg.pages:
            embed = discord.Embed(
                colour=discord.Colour.blurple(),
                title=cog.qualified_name if cog else "Unsorted",
            )
            embed.description = f"> {cog.description}\n{desc}" if cog else f"> No description\n{desc}"
            embed.set_footer(text=f'Use "{self.context.clean_prefix}help <command>" for more information.')
            pages.append(embed)

    async def send_bot_help(
        self,
        mapping: Mapping[commands.Cog, list[commands.Group | commands.Command]],
    ) -> None:
        pages = []

        for cog, cmds in mapping.items():
            cmds = await self.filter_commands(cmds, sort=True)
            await self.format_commands(cog, cmds, pages=pages)

        total = len(pages)
        for i, embed in enumerate(pages, start=1):
            embed.title = f"Page {i}/{total}: {embed.title}"

        pg = RoboPages(SimpleListSource(pages), ctx=self.context)
        await pg.start()

    async def send_cog_help(self, cog: commands.Cog) -> None:
        pages = []

        await self.format_commands(cog, await self.filter_commands(cog.get_commands(), sort=True), pages=pages)

        if not pages:
            await self.context.send(f'My "{cog.qualified_name}" cog has no message commands.')
            return

        total = len(pages)
        for i, embed in enumerate(pages, start=1):
            embed.title = f"Page {i}/{total}: {embed.title}"

        pg = RoboPages(SimpleListSource[discord.Embed](pages), ctx=self.context)
        await pg.start()

    async def send_group_help(self, group: commands.Group) -> None:
        try:
            await group.can_run(self.context)
        except (commands.CommandError, commands.CheckFailure):
            await self.context.send(f'No command called "{group.name}" found.')
            return None
        if not group.commands:
            return await self.send_command_help(group)
        subs = "\n".join(f"`{c.qualified_name}`: {c.short_doc}" for c in group.commands)
        embed = discord.Embed(colour=discord.Colour.blurple())
        embed.title = f"{self.context.clean_prefix}{group.qualified_name} {group.signature}"
        embed.description = f"{group.help or ''}\n\n**Subcommands**\n\n{subs}"
        embed.set_footer(text=f'Use "{self.context.clean_prefix}help <command>" for more information.')
        await self.context.send(embed=embed)

    async def send_command_help(self, command: commands.Command) -> None:
        try:
            await command.can_run(self.context)
        except (commands.CommandError, commands.CheckFailure):
            await self.context.send(f'No command called "{command.name}" found.')
            return
        embed = discord.Embed(colour=discord.Colour.blurple())
        embed.title = f"{self.context.clean_prefix}{command.qualified_name} {command.signature}"
        embed.description = command.help or "No help provided"
        embed.set_footer(text=f'Use "{self.context.clean_prefix}help <command>" for more information.')
        await self.context.send(embed=embed)


class Help(commands.Cog):
    """
    Mipha's help command!
    """

    def __init__(self, bot: Mipha) -> None:
        self.bot = bot
        self.bot._original_help_command = bot.help_command
        self.bot.help_command = PaginatedHelpCommand()
        self.bot.help_command.cog = self

    async def cog_unload(self) -> None:
        assert self.bot._original_help_command is not None
        self.bot.help_command = self.bot._original_help_command


async def setup(bot: Mipha) -> None:
    await bot.add_cog(Help(bot))
