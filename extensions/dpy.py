from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

import discord
import mystbin
from discord import app_commands
from discord.ext import commands
import re

from utilities.shared.ui import BaseView

if TYPE_CHECKING:
    from bot import Mipha
    from utilities.context import Interaction


CODE_BLOCK_PATTERN = re.compile(r'```(?:py|python)\b\s*([\s\S]*?)```')


class PasteView(BaseView):
    def __init__(self, paste: mystbin.Paste, /, *, author_id: int) -> None:
        super().__init__(timeout=datetime.timedelta(hours=24).seconds)
        self.paste = paste
        self.author_id = author_id
        self.add_item(discord.ui.Button(style=discord.ButtonStyle.url, label="Paste URL", url=paste.url))

    @discord.ui.button(label="Delete this paste", style=discord.ButtonStyle.red, emoji="\U0001f5d1\U0000fe0f")
    async def delete_button(self, interaction: Interaction, _: discord.ui.Button) -> None:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "You are not the author of the message/creator of the paste.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.paste.delete()
        await interaction.followup.send("Deleted!", ephemeral=True)


class Dpy(commands.Cog):
    def __init__(self, bot: Mipha, /) -> None:
        self.bot = bot
        self.mystbin_context_menu = app_commands.ContextMenu(
            name="Message to Mystbin", callback=self.to_mystbin_callback, type=discord.AppCommandType.message
        )
        self.bot.tree.add_command(self.mystbin_context_menu)

    async def cog_unload(self) -> None:
        self.bot.tree.remove_command(self.mystbin_context_menu.name, type=self.mystbin_context_menu.type)

    async def to_mystbin_callback(self, interaction: Interaction, message: discord.Message) -> None:
        await interaction.response.defer(ephemeral=False, thinking=True)

        files = [
            mystbin.File(filename=attachment.filename, content=(await attachment.read()).decode("utf-8"))
            for attachment in message.attachments
            if attachment.content_type and attachment.content_type.split("/")[0].lower() == "text"
        ]
        if message.content:
            files.insert(0, mystbin.File(filename="message-contents.txt", content=message.content))

            for i, content in enumerate(CODE_BLOCK_PATTERN.findall(message.content), start=1):
                file = mystbin.File(filename=f"message-contents-code_block_{i}.py", content=content)
                files.append(file)

        paste = await self.bot.mb_client.create_paste(
            files=files, expires=(datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=24))
        )

        view = PasteView(paste, author_id=message.author.id)
        await interaction.followup.send(
            (
                f"I've created that paste for you based on [this message]({message.jump_url})."
                f"\n\nIt will expire in 24 hours, "
                "but the message author can delete it early with the button below!"
            ),
            view=view,
        )


async def setup(bot: Mipha, /) -> None:
    await bot.add_cog(Dpy(bot))
