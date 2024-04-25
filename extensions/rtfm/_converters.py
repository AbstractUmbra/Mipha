from __future__ import annotations

import re
from ssl import CertificateError
from typing import TYPE_CHECKING

import aiohttp
from discord.ext import commands

from extensions.rtfm import _inventory_parser

if TYPE_CHECKING:
    from utilities.context import Context


class ValidURL(commands.Converter):
    """
    Represents a valid webpage URL.

    This converter checks whether the given URL can be reached and requesting it returns a status
    code of 200. If not, `BadArgument` is raised.

    Otherwise, it simply passes through the given URL.
    """

    @staticmethod
    async def convert(ctx: Context, url: str) -> str:
        """This converter checks whether the given URL can be reached with a status code of 200."""
        try:
            async with ctx.bot.session.get(url) as resp:
                if resp.status != 200:
                    raise commands.BadArgument(f"HTTP GET on `{url}` returned status `{resp.status}`, expected 200")
        except CertificateError:
            if url.startswith("https"):
                raise commands.BadArgument(f"Got a `CertificateError` for URL `{url}`. Does it support HTTPS?")
            raise commands.BadArgument(f"Got a `CertificateError` for URL `{url}`.")
        except ValueError:
            raise commands.BadArgument(f"`{url}` doesn't look like a valid hostname to me.")
        except aiohttp.ClientConnectorError:
            raise commands.BadArgument(f"Cannot connect to host with URL `{url}`.")
        return url


class Inventory(commands.Converter):
    """
    Represents an Intersphinx inventory URL.

    This converter checks whether intersphinx accepts the given inventory URL, and raises
    `BadArgument` if that is not the case or if the url is unreachable.

    Otherwise, it returns the url and the fetched inventory dict in a tuple.
    """

    @staticmethod
    async def convert(ctx: Context, url: str) -> tuple[str, _inventory_parser.InventoryDict]:
        """Convert url to Intersphinx inventory URL."""
        await ctx.typing()
        try:
            inventory = await _inventory_parser.fetch_inventory(url, session=ctx.bot.session)
        except _inventory_parser.InvalidHeaderError:
            raise commands.BadArgument("Unable to parse inventory because of invalid header, check if URL is correct.")
        else:
            if inventory is None:
                raise commands.BadArgument(
                    f"Failed to fetch inventory file after {_inventory_parser.FAILED_REQUEST_ATTEMPTS} attempts."
                )
            return url, inventory


class PackageName(commands.Converter):
    """
    A converter that checks whether the given string is a valid package name.

    Package names are used for stats and are restricted to the a-z and _ characters.
    """

    PACKAGE_NAME_RE = re.compile(r"[^a-z0-9_]")

    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> str:
        """Checks whether the given string is a valid package name."""
        if cls.PACKAGE_NAME_RE.search(argument):
            raise commands.BadArgument(
                "The provided package name is not valid; please only use the _, 0-9, and a-z characters."
            )
        return argument
