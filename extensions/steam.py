from __future__ import annotations

import io
from typing import TYPE_CHECKING, TypedDict

import yarl
from aiohttp import FormData
from discord import File, app_commands
from discord.ext import commands

if TYPE_CHECKING:
    from bot import Mipha
    from utilities.context import Interaction


class ModInner(TypedDict):
    publishedfileid: str
    sortorder: int
    filetype: int


class CollectionInner(TypedDict):
    publishedfileid: str
    result: int
    children: list[ModInner]


class InnerResponse(TypedDict):
    result: int
    resultcount: int
    collectiondetails: list[CollectionInner]


class SteamCollectionResponse(TypedDict):
    response: InnerResponse


class Steam(commands.GroupCog):
    def __init__(self, bot: Mipha, /) -> None:
        self.bot: Mipha = bot
        self.collection_url: yarl.URL = yarl.URL("https://api.steampowered.com/ISteamRemoteStorage/GetCollectionDetails/v1/")

    async def make_request(self, form: FormData, /) -> SteamCollectionResponse:
        async with self.bot.session.post(self.collection_url, data=form) as resp:
            return await resp.json()

    @app_commands.command(
        name="mod-ids-from-collection",
        description="Get a file containing a comma delimited list of mod ids from a Steam collection.",
    )
    @app_commands.rename(collection_id="collection")
    @app_commands.describe(collection_id="The collection ID to fetch mod IDs from.")
    async def mod_ids_from_collection(
        self, interaction: Interaction, collection_id: app_commands.Range[int, 1, 4000000000]
    ) -> None:
        await interaction.response.defer()

        form = FormData([("collectioncount", 1), ("publishedfileids[0]", collection_id)])

        resp = await self.make_request(form)

        ret = io.StringIO(
            ",".join(
                mod["publishedfileid"]
                for collection in resp["response"]["collectiondetails"]
                for mod in collection["children"]
            )
        )

        file = File(ret, filename="mod-ids.txt", description=f"The mod ids for collection: {collection_id}")

        return await interaction.followup.send(file=file)


async def setup(bot: Mipha, /) -> None:
    await bot.add_cog(Steam(bot))
