from __future__ import annotations

import io
from shlex import split
from typing import TYPE_CHECKING, Annotated

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from lru import LRU
from rcon.source import rcon

from utilities.shared.formats import to_codeblock

from .status import StatusHandler

if TYPE_CHECKING:
    from bot import Mipha
    from utilities.context import Context, Interaction

    from ._types.config import Details


class MinecraftPlayerFetchError(Exception): ...


class MCServerConverter(commands.Converter["Details | None"]):
    async def convert(self, ctx: Context[Minecraft], argument: str) -> Details | None:
        config = ctx.cog.config

        try:
            resolved = config[argument.lower()]
        except KeyError:
            return None

        return resolved


class Minecraft(commands.GroupCog):
    def __init__(self, bot: Mipha, /, *, config: dict[str, Details]) -> None:
        self.bot = bot
        self.config: dict[str, Details] = config
        self.status_handler = StatusHandler(config)
        self._mc_uuid_cache: LRU[str, str | None] = LRU(256)

    @commands.hybrid_command(aliases=["mc"])
    @app_commands.describe(
        server="Which server to send the command to?", args="The command and command arguments to send to the server."
    )
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    async def minecraft(self, ctx: Context, server: Annotated["Details | None", MCServerConverter], *, args: str) -> None:  # noqa: UP037 # required for converter use
        """Command for quickly executing RCON commands within a managed minecraft server!"""
        async with ctx.typing(ephemeral=True):
            if not server:
                await ctx.send("Sorry, but I don't think I manage that server?", ephemeral=True)
                return None

            split_args = split(args)
            command, *command_args = split_args

            if not command and not command_args:
                return await ctx.send("You need to enter command details.", ephemeral=True)

            if ctx.author.id not in server["ops"]:
                return await ctx.send("You're not authorized to mess with this server.", ephemeral=True)

            output = await rcon(
                command,
                *command_args,
                host=server["host"],
                port=server["port"],
                passwd=server["password"],
                timeout=None,
                enforce_id=False,
            )

            if output:
                output = to_codeblock(output, language="txt", escape_md=False)
                return await ctx.send(
                    f"Command `{command}` with arguments `{' '.join(command_args)}` has output:-\n\n{output}", ephemeral=True
                )

            return await ctx.send(
                f"Command `{command}` with arguments `{' '.join(command_args)}` returned with no output.", ephemeral=True
            )

    async def _uuid_lookup(self, minecraft_username: str) -> str | None:
        try:
            return self._mc_uuid_cache[minecraft_username]
        except KeyError:
            async with (
                aiohttp.ClientSession(connector=aiohttp.TCPConnector(enable_cleanup_closed=True)) as session,
                session.get(f"https://api.mojang.com/users/profiles/minecraft/{minecraft_username}") as resp,
            ):
                resp.raise_for_status()
                if resp.status == 204:
                    uuid = None
                else:
                    data = await resp.json()
                    if "error" in data:
                        raise MinecraftPlayerFetchError(data["error"], data["errorMessage"]) from None
                    uuid = data["id"]

            self._mc_uuid_cache[minecraft_username] = uuid

            return uuid

    @commands.hybrid_command(name="mcavatar")
    @app_commands.describe(username="The minecraft username to search for.")
    async def player_avatar(self, ctx: Context, *, username: str) -> None:
        """Display a minecraft avatar."""
        uuid = await self._uuid_lookup(username)
        if not uuid:
            return await ctx.send(f"No such user {username!r}")

        async with self.bot.session.get(f"https://visage.surgeplay.com/full/512/{uuid}.png") as resp:
            resp.raise_for_status()
            data = await resp.read()

        return await ctx.send(file=discord.File(io.BytesIO(data), filename=f"{username}.png"))

    @commands.hybrid_command(name="status")
    @app_commands.describe(
        server="The minecraft server to query, autocomplete will search my managed servers, but type whatever you need."
    )
    async def server_status(self, ctx: Context, *, server: str) -> None:
        config = self.config.get(server)
        kwargs = {"server_string": server, "server_config": config}
        try:
            status = await self.status_handler.server_status(**kwargs)  # pyright: ignore[reportArgumentType] # im not making a typed dict for this
        except ValueError:
            return await ctx.send("Your server argument was not valid.")

        embed, file = self.status_handler.mcstatus_message(status)

        return await ctx.send(embed=embed, file=file)

    @minecraft.autocomplete("server")
    @server_status.autocomplete("server")
    async def minecraft_server_autocomplete(self, interaction: Interaction, _: str) -> list[app_commands.Choice[str]]:
        ret: list[str] = []

        for name, item in self.config.items():
            if interaction.user.id in item["ops"]:
                ret.append(name)

        return [app_commands.Choice(name=n, value=n) for n in ret]
