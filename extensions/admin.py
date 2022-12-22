"""
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at http://mozilla.org/MPL/2.0/.
"""

from __future__ import annotations

import io
import time
import traceback
from typing import TYPE_CHECKING, Literal

import discord
from discord.ext import commands
from discord.ext.commands import Greedy

from utilities import formats
from utilities.context import Context, GuildContext
from utilities.converters import MystbinPasteConverter


if TYPE_CHECKING:
    from asyncpg import Record

    from bot import Mipha


class Admin(commands.Cog):
    """Admin-only commands that make the bot dynamic."""

    def __init__(self, bot: Mipha) -> None:
        self.bot = bot

    def cleanup_code(self, content: str) -> str:
        """Automatically removes code blocks from the code."""
        if content.startswith("```") and content.endswith("```"):
            return "\n".join(content.split("\n")[1:-1])

        # remove `foo`
        return content.strip("` \n")

    async def cog_check(self, ctx: Context) -> bool:
        return await self.bot.is_owner(ctx.author)

    def get_syntax_error(self, err: SyntaxError) -> str:
        """Grabs the syntax error."""
        if err.text is None:
            return f"```py\n{err.__class__.__name__}: {err}\n```"
        return f'```py\n{err.text}{"^":>{err.offset}}\n{err.__class__.__name__}: {err}```'

    @commands.command()
    @commands.guild_only()
    async def leave(self, ctx: Context) -> None:
        """Leaves the current guild."""
        assert ctx.guild is not None
        await ctx.guild.leave()

    @commands.command()
    async def load(self, ctx: Context, *, module: str) -> None:
        """Loads a module."""
        module = f"extensions.{module}"

        try:
            await self.bot.load_extension(module)
        except commands.ExtensionError as err:
            await ctx.send(f"{err.__class__.__name__}: {err}")
        else:
            await ctx.message.add_reaction(ctx.tick(True))

    @commands.command()
    async def unload(self, ctx: Context, *, module: str) -> None:
        """Unloads a module."""
        module = f"extensions.{module}"

        try:
            await self.bot.unload_extension(module)
        except commands.ExtensionError as err:
            await ctx.send(f"{err.__class__.__name__}: {err}")
        else:
            await ctx.message.add_reaction(ctx.tick(True))

    @commands.command(name="reload")
    async def _reload(self, ctx: Context, *, module: str) -> None:
        """Reloads a module."""
        module = f"extensions.{module}"

        try:
            await self.bot.reload_extension(module)
        except commands.ExtensionNotLoaded:
            return await self.bot.load_extension(module)
        except commands.ExtensionError as err:
            await ctx.send(f"{err.__class__.__name__}: {err}")
            await ctx.message.add_reaction(ctx.tick(False))
            return

        await ctx.message.add_reaction(ctx.tick(True))

    @commands.group(invoke_without_command=True)
    async def sql(self, ctx: Context, *, query: str) -> None:
        """Run some SQL."""
        query = self.cleanup_code(query)

        is_multistatement = query.count(";") > 1
        if is_multistatement:
            # fetch does not support multiple statements
            strategy = ctx.db.execute
        else:
            strategy = ctx.db.fetch

        try:
            start = time.perf_counter()
            results = await strategy(query)
            dati = (time.perf_counter() - start) * 1000.0
        except Exception:
            await ctx.send(f"```py\n{traceback.format_exc()}\n```")
            return

        rows = len(results)
        if isinstance(results, str) or rows == 0:
            await ctx.send(f"`{dati:.2f}ms: {results}`")
            return

        assert isinstance(results, list)

        headers = list(results[0].keys())
        table = formats.TabularData()
        table.set_columns(headers)
        table.add_rows(list(r.values()) for r in results)
        render = table.render()

        fmt = f"```\n{render}\n```\n*Returned {formats.plural(rows):row} in {dati:.2f}ms*"
        if len(fmt) > 2000:
            filep = io.BytesIO(fmt.encode("utf-8"))
            await ctx.send("Too many results...", file=discord.File(filep, "results.txt"))
        else:
            await ctx.send(fmt)

    @sql.command(name="table")
    async def sql_table(self, ctx: Context, *, table_name: str) -> None:
        """Runs a query describing the table schema."""
        query = """SELECT column_name, data_type, column_default, is_nullable
                   FROM INFORMATION_SCHEMA.COLUMNS
                   WHERE table_name = $1
                """

        results: list[Record] = await ctx.db.fetch(query, table_name)

        headers = list(results[0].keys())
        table = formats.TabularData()
        table.set_columns(headers)
        table.add_rows(list(r.values()) for r in results)
        render = table.render()

        fmt = f"```\n{render}\n```"
        if len(fmt) > 2000:
            filep = io.BytesIO(fmt.encode("utf-8"))
            await ctx.send("Too many results...", file=discord.File(filep, "results.txt"))
        else:
            await ctx.send(fmt)

    @commands.command()
    @commands.guild_only()
    async def sync(
        self, ctx: GuildContext, guilds: Greedy[discord.Object], spec: Literal["~", "*", "^"] | None = None
    ) -> None:
        """
        Pass guild ids or pass a sync specification:-

        `~` -> Current guild.
        `*` -> Copies global to current guild.
        `^` -> Clears all guild commands.
        """
        if not guilds:
            if spec == "~":
                fmt = await ctx.bot.tree.sync(guild=ctx.guild)
            elif spec == "*":
                ctx.bot.tree.copy_global_to(guild=ctx.guild)
                fmt = await ctx.bot.tree.sync(guild=ctx.guild)
            elif spec == "^":
                ctx.bot.tree.clear_commands(guild=ctx.guild)
                await ctx.bot.tree.sync(guild=ctx.guild)
                fmt = []
            else:
                fmt = await ctx.bot.tree.sync()

            await ctx.send(
                f"Synced {formats.plural(len(fmt)):command} {'globally' if spec is None else 'to the current guild.'}"
            )
            return

        ret = 0
        for guild in guilds:
            try:
                await ctx.bot.tree.sync(guild=guild)
            except discord.HTTPException:
                pass
            else:
                ret += 1

        await ctx.send(f"Synced the tree to {formats.plural(ret):guild}.")

    @commands.command(name="delete_paste", aliases=["dp"])
    @commands.guild_only()
    async def delete_paste(
        self,
        ctx: GuildContext,
        *,
        paste: str = commands.param(converter=MystbinPasteConverter, description="Paste url or ID"),
    ) -> None:
        await ctx.bot.mb_client.delete_paste(paste)


async def setup(bot: Mipha) -> None:
    """Cog entrypoint."""
    await bot.add_cog(Admin(bot))
