"""
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at http://mozilla.org/MPL/2.0/.
"""

from __future__ import annotations

import datetime
import secrets
import traceback
from typing import TYPE_CHECKING

import discord
from discord import app_commands


if TYPE_CHECKING:
    from typing_extensions import Self

    from utilities.context import Interaction

__all__ = ("MiphaBaseView", "ConfirmationView")


class MiphaBaseModal(discord.ui.Modal):
    async def on_error(self, interaction: Interaction, error: app_commands.AppCommandError) -> None:
        e = discord.Embed(title="IRLs Modal Error", colour=0xA32952)
        e.add_field(name="Modal", value=self.__class__.__name__, inline=False)

        (exc_type, exc, tb) = type(error), error, error.__traceback__
        trace = "\n".join(traceback.format_exception(exc_type, exc, tb))

        e.add_field(name="Error", value=f"```py\n{trace}\n```")
        e.timestamp = datetime.datetime.now(datetime.timezone.utc)

        stats: Stats = client.get_cog("Stats")  # type: ignore
        try:
            await stats.webhook.send(embed=e)
        except discord.HTTPException:
            pass


class MiphaBaseView(discord.ui.View):
    message: discord.Message | discord.PartialMessage

    async def on_error(self, interaction: Interaction, error: Exception, item: discord.ui.Item[Self], /) -> None:
        view_name = self.__class__.__name__
        interaction.client.log_handler.log.exception("Exception occurred in View %r:\n%s", view_name, error)

        embed = discord.Embed(title=f"{view_name} View Error", colour=0xA32952)
        embed.add_field(name="Author", value=interaction.user, inline=False)
        channel = interaction.channel
        guild = interaction.guild
        location_fmt = f"Channel: {channel.name} ({channel.id})"  # type: ignore

        if guild:
            location_fmt += f"\nGuild: {guild.name} ({guild.id})"
            embed.add_field(name="Location", value=location_fmt, inline=True)

        (exc_type, exc, tb) = type(error), error, error.__traceback__
        trace = traceback.format_exception(exc_type, exc, tb)
        clean = "".join(trace)
        if len(clean) >= 2000:
            password = secrets.token_urlsafe(16)
            paste = await interaction.client.mb_client.create_paste(filename="error.py", content=clean, password=password)
            embed.description = (
                f"Error was too long to send in a codeblock, so I have pasted it [here]({paste.url})."
                f"\nThe password is `{password}`."
            )
        else:
            embed.description = f"```py\n{clean}\n```"

        embed.timestamp = datetime.datetime.now(datetime.timezone.utc)
        await interaction.client.logging_webhook.send(embed=embed)
        await interaction.client.owner.send(embed=embed)

    def _disable_all_buttons(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True

    async def on_timeout(self) -> None:
        self._disable_all_buttons()
        await self.message.edit(view=self)


class ConfirmationView(MiphaBaseView):
    def __init__(self, *, timeout: float, author_id: int, delete_after: bool) -> None:
        super().__init__(timeout=timeout)
        self.value: bool | None = None
        self.delete_after: bool = delete_after
        self.author_id: int = author_id
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user and interaction.user.id == self.author_id:
            return True
        else:
            await interaction.response.send_message("This confirmation dialog is not for you.", ephemeral=True)
            return False

    async def on_timeout(self) -> None:
        if self.delete_after and self.message:
            if not self.message.flags.ephemeral:
                await self.message.delete()
            else:
                await self.message.edit(view=None, content="This is safe to dismiss now.")

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: Interaction, button: discord.ui.Button) -> None:
        self.value = True
        await interaction.response.defer()
        if self.delete_after and self.message:
            await interaction.delete_original_response()
        else:
            await interaction.edit_original_response(view=None)

        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red)
    async def cancel(self, interaction: Interaction, button: discord.ui.Button) -> None:
        self.value = False
        await interaction.response.defer()
        if self.delete_after and self.message:
            await interaction.delete_original_response()
        else:
            await interaction.edit_original_response(view=None)

        self.stop()
