from __future__ import annotations

import datetime
from typing import Generator, TYPE_CHECKING

import discord
import mystbin
from discord import app_commands
from discord.ext import commands

from utilities.shared.ui import BaseView

if TYPE_CHECKING:
    from bot import Mipha
    from utilities.context import Interaction


# helper function to extract codeblocks
def codeblock_converter(text: str) -> Generator[str]:
    in_codeblock: bool = False
    curr_index: int = 0
    start_index: int = 0

    # -2 as there are 3 backticks, so we want to stop at the first backtick to prevent index out of range
    while curr_index < len(text) - 2:
        # check if this is the start/end of a codeblock
        if not (text[curr_index] == '`' and text[curr_index + 1] == '`' and text[curr_index + 2] == '`'):
            curr_index += 1
            continue

        if in_codeblock:
            yield text[start_index : curr_index]
            in_codeblock = False
            curr_index += 3 # jump outside of codeblock
        else:
            curr_index += 3 # jump to start of codeblock

            # traverse until we hit a newline or a space
            # if we hit a newline, it means it is a language hint (e.g, ```py\n...text```)
            # then we do not include it
            # else we include it as part of the codeblock (e.g, ```text``)
            temp_index: int = curr_index

            # -4 to help with early exits
            while temp_index < len(text) - 4:
                if text[temp_index] == ' ':
                    break
                # check if this is a language hint
                if text[temp_index] == '\n':
                    # jump to the start as we don't want to include the language hint
                    curr_index = temp_index + 1 # + 1 to remove the newline
                    break
                temp_index += 1
            start_index = curr_index
            in_codeblock = True


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

        files = []
        if message.content:
            files.append(mystbin.File(filename="message-contents.txt", content=message.content))

            for idx, codeblock in enumerate(codeblock_converter(message.content), start=1):
                # handle edge-cases like empty codeblocks (i.e., ``````)
                if not codeblock:
                    continue
                file = mystbin.File(filename=f"message-contents-code_block_{idx}.py", content=codeblock)
                files.append(file)

        for attachment in message.attachments:
            if not attachment.content_type or attachment.content_type.split("/")[0].lower() != "text":
                continue

            file = mystbin.File(filename=attachment.filename, content=(await attachment.read()).decode("utf-8"))
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
