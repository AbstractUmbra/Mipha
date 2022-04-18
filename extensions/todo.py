"""
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at http://mozilla.org/MPL/2.0/.
"""
from __future__ import annotations

from textwrap import shorten
from typing import TYPE_CHECKING

import asyncpg
import discord
from discord.ext import commands

from utilities.context import Context
from utilities.paginator import RoboPages, SimpleListSource


if TYPE_CHECKING:
    from bot import Kukiko


class Todo(commands.Cog):
    """
    A cog for 'todo' management and information.
    """

    def __init__(self, bot: Kukiko) -> None:
        self.bot = bot

    def _gen_todos(self, records: list[asyncpg.Record]) -> list[discord.Embed]:
        descs = []
        list_of_records = [records[x : x + 10] for x in range(0, len(records), 10)]
        for records in list_of_records:
            descs.append(
                discord.Embed(
                    description="\n".join(
                        [
                            f"[__`{record['id']}`__]({record['jump_url']}): {discord.utils.format_dt(record['added_at'], 'R')} :: {shorten(record['content'], width=100)}"
                            for record in records
                        ]
                    )
                ).set_footer(text="Use todo info ## for more details.")
            )
        return descs

    @commands.group(invoke_without_command=True)
    async def todo(self, ctx: Context, *, content: str | None = None) -> None:
        """Todos! See the subcommands for more info."""
        if not ctx.invoked_subcommand:
            if not content:
                await ctx.send_help(ctx.command)
            else:
                await self.todo_add(ctx, content=content)

    @todo.command(name="list", cooldown_after_parsing=True)
    @commands.max_concurrency(1, commands.BucketType.user)
    @commands.cooldown(1, 15, commands.BucketType.user)
    async def todo_list(self, ctx: Context) -> None:
        """A list of todos for you."""
        query = """ SELECT * FROM todos WHERE owner_id = $1 ORDER BY id ASC LIMIT 100; """
        records = await self.bot.pool.fetch(query, ctx.author.id)

        if not records:
            await ctx.send("You appear to have no active todos, look at how productive you are.")
            return
        embeds = self._gen_todos(records)
        pages = RoboPages(source=SimpleListSource(embeds), ctx=ctx)
        await pages.start()

    @commands.command(name="todos")
    async def alt_todo_list(self, ctx: Context) -> None:
        """Alias of `todo list`."""
        await self.todo_list(ctx)

    @todo.command(name="add")
    async def todo_add(self, ctx: Context, *, content: str) -> None:
        """Add me something to do later..."""
        query = """
                INSERT INTO todos (owner_id, content, added_at, jump_url)
                VALUES ($1, $2, $3, $4)
                RETURNING id;
                """
        succeed: asyncpg.Record = await self.bot.pool.fetchrow(
            query,
            ctx.author.id,
            content,
            ctx.message.created_at,
            ctx.message.jump_url,
        )
        if succeed["id"]:
            await ctx.send(f"{ctx.tick(True)}: created todo #__`{succeed['id']}`__ for you!")

    @todo.command(name="delete", aliases=["remove", "bin", "done"])
    async def todo_delete(self, ctx: Context, todo_ids: commands.Greedy[int]) -> None:
        """Delete my todo thanks, since I did it already."""
        query = """ DELETE FROM todos WHERE owner_id = $1 AND id = $2 RETURNING id; """
        if not todo_ids:
            await ctx.send("You must provide some numbers...")
            return

        iterable = [(ctx.author.id, td) for td in todo_ids]
        try:
            await self.bot.pool.executemany(query, iterable)
        finally:
            await ctx.send(
                f"Okay well done. I removed the __**`#{'`**__, __**`#'.join(str(tid) for tid in todo_ids)}`**__ todo{'s' if len(todo_ids) > 1 else ''} for you."
            )

    @todo.command(name="edit")
    async def todo_edit(self, ctx: Context, todo_id: int, *, content: str) -> None:
        """Edit my todo because I would like to change the wording or something."""
        owner_check = """
                      SELECT id, owner_id
                      FROM todos
                      WHERE owner_id = $1
                      AND id = $2;
                      """
        owner = await self.bot.pool.fetchrow(owner_check, ctx.author.id, todo_id)

        if not owner or owner["owner_id"] != ctx.author.id:
            await ctx.send("That doesn't seem to be your todo, or the ID is incorrect.")
            return

        update_query = """ UPDATE todos SET content = $2, jump_url = $3 WHERE id = $1 RETURNING id; """
        success = await self.bot.pool.fetchrow(update_query, todo_id, content, ctx.message.jump_url)
        if success:
            await ctx.send(f"Neat. So todo #__`{success['id']}`__ has been updated for you. Go be productive!")

    @todo.command(name="info")
    async def todo_info(self, ctx: Context, todo_id: int) -> None:
        """Get a little extra info..."""
        query = """
                SELECT *
                FROM todos
                WHERE owner_id = $1
                AND id = $2;
                """
        record = await self.bot.pool.fetchrow(query, ctx.author.id, todo_id)

        if not record:
            await ctx.send("No record for by you with that ID. Is it correct?")
            return

        embed = discord.Embed(title="Extra todo info")
        embed.description = f"{record['content']}\n[Message link!]({record['jump_url']})"
        embed.timestamp = record["added_at"]
        embed.set_author(name=ctx.author.name, icon_url=ctx.author.display_avatar.url)
        await ctx.send(embed=embed)

    @todo.command(name="clear")
    @commands.cooldown(1, 60, commands.BucketType.user)
    async def todo_clear(self, ctx: Context) -> None:
        """Lets wipe 'em all!"""
        query = """
                DELETE FROM todos
                WHERE owner_id = $1;
                """
        confirm = await ctx.prompt("This will wipe your todos from my memory. Are you sure?")

        if not confirm:
            return

        await self.bot.pool.execute(query, ctx.author.id)
        await ctx.message.add_reaction(ctx.tick(True))

    @todo_list.error
    @todo_clear.error
    async def todo_errors(self, ctx: Context, error: commands.CommandError) -> None:
        """Error handler for specific shit."""
        error = getattr(error, "original", error)
        if isinstance(error, commands.MaxConcurrencyReached):
            await ctx.send("Whoa, I know you're eager but close your active list first!")


async def setup(bot):
    await bot.add_cog(Todo(bot))
