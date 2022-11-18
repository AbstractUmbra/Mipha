from __future__ import annotations

import re
from typing import TYPE_CHECKING

import discord
import yarl
from discord import ui
from discord.ext import commands

from utilities.ui import MiphaBaseView


if TYPE_CHECKING:
    from typing_extensions import Self

    from bot import Mipha

    from .tiktok import TiktokCog

TWITTER_URL_REGEX = re.compile(r"https?://twitter\.com/(?P<user>\w+)/status/(?P<id>\d+)")


class RepostView(MiphaBaseView):
    message: discord.Message

    def __init__(self, urls: list[yarl.URL], /, *, timeout: float | None = 180, cog: TiktokCog | None = None) -> None:
        self.urls: list[yarl.URL] = urls
        self.tiktok: TiktokCog | None = cog
        super().__init__(timeout=timeout)
        if self.tiktok is None:
            self.download_video.disabled = True

    async def on_timeout(self) -> None:
        await self.message.edit(view=None)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: ui.Item[Self], /) -> None:
        client: Mipha = interaction.client  # type: ignore
        await client.tree.on_error(interaction, error)  # type: ignore

    @ui.button(label="Repost?", emoji="\U0001f503")
    async def repost_button(self, interaction: discord.Interaction, button: discord.ui.Button[Self]) -> None:
        first_url = self.urls.pop(0)
        await interaction.response.send_message(content=str(first_url))
        self._disable_all_buttons()
        await self.message.edit(view=self)

        if self.urls:
            for url in self.urls:
                await interaction.channel.send(str(url))  # type: ignore # it's definitely not a stagechannel thanks

    @ui.button(label="Upload video?", emoji="\U0001f4fa")
    async def download_video(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        assert self.tiktok
        assert interaction.guild  # covered in the guard in message

        await interaction.response.defer(thinking=True)
        self._disable_all_buttons()
        await self.message.edit(view=self)

        url = self.urls.pop(0)

        _info = await self.tiktok._extract_video_info(str(url))
        if not _info:
            await interaction.followup.send(content="I couldn't grab the video details.")
            self.repost_button.disabled = False
            await self.message.edit(view=self)
            return

        file, _ = await self.tiktok._manipulate_video(_info, filesize_limit=interaction.guild.filesize_limit)
        await interaction.followup.send("I downloaded the video for you:-", file=file)


class Twitter(commands.Cog):
    def __init__(self, bot: Mipha, /) -> None:
        self.bot: Mipha = bot

    def _pull_matches(self, message: discord.Message, /) -> list[re.Match[str]] | None:
        return list(TWITTER_URL_REGEX.finditer(message.content))

    def _handle_url(self, match: re.Match[str]) -> yarl.URL:
        url = yarl.URL(match[0])
        return url.with_host("fxtwitter.com").with_scheme("https")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message, /) -> None:
        if not message.guild or message.guild.id not in {174702278673039360, 149998214810959872}:
            return

        if message.embeds:
            has_video = any(embed.video for embed in message.embeds)
        else:
            has_video = False

        if not has_video:
            return

        matches = self._pull_matches(message)
        if not matches:
            return

        new_urls = [self._handle_url(match) for match in matches]
        tiktok_cog: TiktokCog | None = self.bot.get_cog("TiktokCog")  # type: ignore # this can't be narrowed
        view = RepostView(new_urls, cog=tiktok_cog)
        view.message = await message.reply(
            content="I found twitter links with videos in them. Should I repost them?", view=view, mention_author=False
        )


async def setup(bot: Mipha, /) -> None:
    await bot.add_cog(Twitter(bot))
