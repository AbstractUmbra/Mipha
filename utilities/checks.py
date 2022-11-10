"""
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at http://mozilla.org/MPL/2.0/.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, Callable, TypeVar

from discord import app_commands
from discord.ext import commands


if TYPE_CHECKING:
    from discord.ext.commands._types import Check

    from utilities.context import GuildContext


T = TypeVar("T")


async def check_permissions(
    ctx: GuildContext, perms: dict[str, bool], *, check: Callable[[Iterable[Any]], bool] = all
) -> bool:
    is_owner = await ctx.bot.is_owner(ctx.author)
    if is_owner:
        return True

    resolved = ctx.channel.permissions_for(ctx.author)
    return check(getattr(resolved, name, None) == value for name, value in perms.items())


def has_permissions(*, check: Callable[[Iterable[Any]], bool] = all, **perms: bool) -> Callable[[T], T]:
    async def pred(ctx: GuildContext) -> bool:
        return await check_permissions(ctx, perms, check=check)

    return commands.check(pred)


async def check_guild_permissions(
    ctx: GuildContext,
    perms: dict[str, bool],
    *,
    check: Callable[[Iterable[Any]], bool] = all,
) -> bool:
    is_owner = await ctx.bot.is_owner(ctx.author)
    if is_owner:
        return True

    if ctx.guild is None:
        return False

    resolved = ctx.author.guild_permissions
    return check(getattr(resolved, name, None) == value for name, value in perms.items())


def has_guild_permissions(*, check: Callable[[Iterable[Any]], bool] = all, **perms) -> Callable[[T], T]:
    async def pred(ctx: GuildContext) -> bool:
        return await check_guild_permissions(ctx, perms, check=check)

    return commands.check(pred)


def hybrid_permissions_check(**perms: bool) -> Callable[[T], T]:
    async def pred(ctx: GuildContext) -> bool:
        return await check_guild_permissions(ctx, perms)

    def decorator(func: T) -> T:
        commands.check(pred)(func)
        app_commands.default_permissions(**perms)(func)
        return func

    return decorator


def is_manager() -> Callable[[T], T]:
    return hybrid_permissions_check(manage_guild=True)


def is_mod() -> Callable[[T], T]:
    return hybrid_permissions_check(ban_members=True, manage_messages=True)


def is_admin() -> Callable[[T], T]:
    return hybrid_permissions_check(administrator=True)


def is_in_guilds(*guild_ids: int) -> Check[GuildContext]:
    def predicate(ctx: GuildContext) -> bool:
        guild = ctx.guild
        if guild is None:
            return False
        return guild.id in guild_ids

    return commands.check(predicate)


def mod_or_permissions(**perms) -> Callable[[T], T]:
    perms["manage_guild"] = True

    async def predicate(ctx: GuildContext) -> bool:
        return await check_guild_permissions(ctx, perms, check=any)

    return commands.check(predicate)


def admin_or_permissions(**perms) -> Callable[[T], T]:
    perms["administrator"] = True

    async def predicate(ctx: GuildContext) -> bool:
        return await check_guild_permissions(ctx, perms, check=any)

    return commands.check(predicate)


def can_use_spoiler() -> Callable[[T], T]:
    def predicate(ctx: GuildContext) -> bool:
        if ctx.guild is None:
            raise commands.BadArgument("Cannot be used in private messages.")

        my_permissions = ctx.channel.permissions_for(ctx.guild.me)
        if not (my_permissions.read_message_history and my_permissions.manage_messages and my_permissions.add_reactions):
            raise commands.BadArgument(
                "Need Read Message History, Add Reactions and Manage Messages "
                "to permission to use this. Sorry if I spoiled you."
            )
        return True

    return commands.check(predicate)
