from __future__ import annotations

import re
from typing import TYPE_CHECKING

import discord
import yarl
import yt_dlp
from discord import ui
from discord.ext import commands

from utilities.ui import MiphaBaseView


if TYPE_CHECKING:
    from typing_extensions import Self

    from bot import Mipha

    from .tiktok import TiktokCog

TWITTER_URL_REGEX: re.Pattern[str] = re.compile(r"https?://twitter\.com/(?P<user>\w+)/status/(?P<id>\d+)")
GUILDS: list[discord.Object] = [
    discord.Object(id=174702278673039360),
    discord.Object(id=149998214810959872),
]


class RepostView(MiphaBaseView):
    message: discord.InteractionMessage

    def __init__(
        self,
        urls: list[yarl.URL],
        /,
        *,
        timeout: float | None = 10,
        cog: TiktokCog | None = None,
        owner_id: int,
        target_message: discord.Message,
    ) -> None:
        self.urls: list[yarl.URL] = urls
        self.tiktok: TiktokCog | None = cog
        self.owner_id: int = owner_id
        self.target_message: discord.Message = target_message
        super().__init__(timeout=timeout)
        if self.tiktok is None:
            self.download_video.disabled = True

    async def on_timeout(self) -> None:
        await self.message.delete()

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

        await interaction.response.defer()
        await self.message.delete()

        url = self.urls.pop(0)

        try:
            _info = await self.tiktok._extract_video_info(url)
        except yt_dlp.utils.DownloadError as error:
            await interaction.followup.send(
                "Sorry downloading this video broke somehow. Umbra knows don't worry.", ephemeral=True
            )
            await interaction.client.tree.on_error(interaction, error)  # type: ignore
            return

        if not _info:
            await interaction.followup.send(content="I couldn't grab the video details.")
            self.repost_button.disabled = False
            await self.message.edit(view=self)
            return

        file, _ = await self.tiktok._manipulate_video(_info, filesize_limit=interaction.guild.filesize_limit)
        await self.target_message.reply(content="I downloaded the video for you:-", file=file)

    @ui.button(label="No thanks", style=discord.ButtonStyle.danger, row=2, emoji="\U0001f5d1\U0000fe0f")
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button[RepostView]) -> None:
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message(
                "You're not allowed to close this, only the message author can!", ephemeral=True
            )
        await self.message.delete()
        self.stop()


class Twitter(commands.Cog):
    def __init__(self, bot: Mipha, /) -> None:
        self.bot: Mipha = bot
        self.context_menu = discord.app_commands.ContextMenu(
            name="Process Twitter links", callback=self.context_menu_callback, nsfw=False
        )
        self.bot.tree.add_command(self.context_menu, guilds=GUILDS, override=True)

    def cog_unload(self) -> None:
        self.bot.tree.remove_command(self.context_menu.name, type=self.context_menu.type)

    def _pull_matches(self, message: discord.Message, /) -> list[re.Match[str]]:
        return list(TWITTER_URL_REGEX.finditer(message.content))

    def _handle_url(self, match: re.Match[str]) -> yarl.URL:
        url = yarl.URL(match[0])
        if url.host in ("www.instagram.com", "instagram.com"):
            return url.with_host("ddinstagram.com").with_scheme("https")
        return url.with_host("fxtwitter.com").with_scheme("https")

    async def context_menu_callback(self, interaction: discord.Interaction, message: discord.Message, /) -> None:
        matches = self._pull_matches(message)
        if not matches:
            return await interaction.response.send_message("No valid Twitter URLs found in this message.", ephemeral=True)

        if message.embeds:
            has_video = any(embed.video for embed in message.embeds)
        else:
            has_video = False

        if not has_video:
            return await interaction.response.send_message("There's a Twitter URL but no video, sorry.", ephemeral=True)

        await interaction.response.defer(thinking=False)

        new_urls = [self._handle_url(match) for match in matches]
        tiktok_cog: TiktokCog | None = self.bot.get_cog("TiktokCog")  # type: ignore # narrowing fails
        view = RepostView(new_urls, cog=tiktok_cog, owner_id=interaction.user.id, target_message=message)
        view.message = await interaction.edit_original_response(
            content="I have found Twitter links in your message with video. Should I repost them?",
            view=view,
        )


async def setup(bot: Mipha, /) -> None:
    await bot.add_cog(Twitter(bot))
