from __future__ import annotations

import datetime
import logging
import re
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from utilities.shared.formats import ts
from utilities.shared.paste import CreatePasteInput, Paste
from utilities.shared.ui import BaseView

if TYPE_CHECKING:
    from bot import Mipha
    from utilities.context import Interaction

CODEBLOCK_RE = re.compile(r"```(?P<lang>\w+)?\n(?P<content>.*?)```", re.DOTALL)
BLANK_LINES_RE = re.compile(r"\n\s*\n+")
LOGGER = logging.getLogger(__name__)


class Codeblock:
    def __init__(self, match: re.Match[str], /) -> None:
        self._match = match
        self.language: str = match["lang"] or ""
        self.content: str = match["content"]

    def __repr__(self) -> str:
        return f"<Codeblock language={self.language} span={self.span()}>"

    def __str__(self) -> str:
        return self.to_string()

    def to_string(self) -> str:
        return f"```{self.language}\n{self.content}\n```"

    def span(self) -> tuple[int, int]:
        return self._match.span()


def collapse_blank_lines(text: str, max_blank: int = 1) -> str:
    replacement = "\n" * (max_blank + 1)
    return BLANK_LINES_RE.sub(replacement, text)


def extract_codeblocks_with_placeholders(input_: str) -> tuple[str, list[Codeblock]]:
    codeblocks = []
    output = []
    last = 0
    idx = 1

    for idx, match in enumerate(CODEBLOCK_RE.finditer(input_), start=1):
        start, end = match.span()

        # keep text before the codeblock
        output.extend([input_[last:start], f"<Codeblock #{idx} extracted here>"])

        # extract codeblock
        codeblocks.append(Codeblock(match))

        last = end

    # append remaining text
    output.append(input_[last:])

    return "\n".join(output), codeblocks


class PasteView(BaseView):
    def __init__(self, paste: Paste, /, *, author_id: int) -> None:
        super().__init__(timeout=datetime.timedelta(hours=24).seconds)
        self.paste = paste
        self.author_id = author_id
        self.add_item(discord.ui.Button(style=discord.ButtonStyle.url, label="Paste URL", url=paste.url))

    @discord.ui.button(label="Delete this paste", style=discord.ButtonStyle.red, emoji="\U0001f5d1\U0000fe0f")
    async def delete_button(self, interaction: Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "You are not the author of the message/creator of the paste.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.paste.delete(
            self.paste.id,
            session=interaction.client.session,
            api_token=interaction.client.config.get("tokens", {}).get("paste", ""),
        )

        button.label = "Paste deleted"
        button.disabled = True
        if interaction.message is not None:
            try:
                await interaction.message.edit(view=button.view)
            except discord.NotFound:
                pass

        await interaction.followup.send("Deleted!", ephemeral=True)


class Dpy(commands.Cog):
    def __init__(self, bot: Mipha, /) -> None:
        self.bot = bot
        self.mystbin_context_menu = app_commands.ContextMenu(
            name="Message to Paste site",
            callback=self.to_pasters_callback,
            type=discord.AppCommandType.message,
            allowed_contexts=discord.app_commands.AppCommandContext(guild=True, dm_channel=True, private_channel=True),
            allowed_installs=discord.app_commands.AppInstallationType(guild=True, user=True),
        )
        self.bot.tree.add_command(self.mystbin_context_menu)

    async def cog_unload(self) -> None:
        self.bot.tree.remove_command(self.mystbin_context_menu.name, type=self.mystbin_context_menu.type)

    async def to_pasters_callback(self, interaction: Interaction, message: discord.Message) -> None:
        await interaction.response.defer(ephemeral=False, thinking=True)
        files: list[CreatePasteInput] = []

        if message.content:
            contents, codeblocks = extract_codeblocks_with_placeholders(message.content)
            if contents:
                files.append(CreatePasteInput(code=contents, title="message-contents", language="txt"))
            for idx, cb in enumerate(codeblocks, start=1):
                files.append(CreatePasteInput(code=cb.content, title=f"codeblock-{idx}.{cb.language}", language=cb.language))

        for attachment in message.attachments:
            if not attachment.content_type or attachment.content_type.split("/")[0].lower() != "text":
                continue
            files.append(
                CreatePasteInput(
                    code=(await attachment.read()).decode("utf-8"),
                    title=attachment.filename,
                    language=attachment.filename.rsplit(".")[-1],
                )
            )

        LOGGER.debug("files: %r", files)

        expiry = datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=24)
        paste = await self.bot.create_paste(
            contents=files, expires=expiry, title=f"{message.author.display_name}'s message repost"
        )

        view = PasteView(paste, author_id=message.author.id)
        await interaction.followup.send(
            (
                f"I've created that paste for you based on [this message]({message.jump_url})."
                f"\n\nIt will expire in {ts(expiry):R}, "
                "but the message author can delete it early with the button below!"
            ),
            view=view,
        )


async def setup(bot: Mipha, /) -> None:
    await bot.add_cog(Dpy(bot))
