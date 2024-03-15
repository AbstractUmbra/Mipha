from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from utilities.shared.ui import BaseView

HYRULE_GUILD_ID = 705500489248145459
_IRL_FRIEND_SERVER = 174702278673039360
HONDANA_ROLE_ID = 1086537644093231144
GREAT_ASSET_ROLE_ID = 1189005762790441010
HELLDIVERS_2_ROLE_ID = 1217496141311250572
ROLE_ASSIGNMENT_CHANNEL_ID = 1086540538112647229
ROLE_ASSIGNMENT_MESSAGE_ID = 1086545767356977173


if TYPE_CHECKING:
    from typing import Self

    from bot import Mipha
    from utilities.context import GuildContext, Interaction


class HyruleRoleAssignmentView(BaseView):
    def __init__(self, bot: Mipha, /) -> None:
        super().__init__(timeout=None)
        self.bot: Mipha = bot
        self._descriptions: dict[str, str] = {
            c.label: c.callback.callback.__doc__.format(owner=self.bot.owner.display_name)
            for c in self.children
            if isinstance(c, discord.ui.Button) and c.callback.callback.__doc__ and c.label
        }

    def sanitise_user(self, member: discord.Member) -> None:
        guild = self.bot.get_guild(_IRL_FRIEND_SERVER)
        assert guild

        if guild.get_member(member.id):
            raise TypeError("No shitposting.")

    async def interaction_check(self, interaction: Interaction, /) -> bool:
        assert isinstance(interaction.user, discord.Member)

        try:
            self.sanitise_user(interaction.user)
        except TypeError:
            await interaction.response.send_message("No shitposting you fucks.", ephemeral=True)
            return False
        return True

    @discord.ui.button(
        label="Hondana",
        custom_id="HyruleHondana__",
        style=discord.ButtonStyle.blurple,
        emoji="\U0001f4da",
        row=0,
    )
    async def add_hondana_role(self, interaction: Interaction, item: discord.ui.Button[Self]) -> None:
        """This role will allow you to send messages in the Hondana category. Pings will be issued on major events."""
        assert isinstance(interaction.user, discord.Member)

        if interaction.user.get_role(HONDANA_ROLE_ID):
            await interaction.response.send_message("You already have the Hondana role!", ephemeral=True)
            return

        await interaction.user.add_roles(discord.Object(id=HONDANA_ROLE_ID))

    @discord.ui.button(
        label="Great Asset",
        custom_id="HyruleGreatAsset__",
        style=discord.ButtonStyle.blurple,
        emoji="\U0001f480",
        row=0,
    )
    async def add_great_asset_role(self, interaction: Interaction, item: discord.ui.Button[Self]) -> None:
        """This role will allow you to send messages in the Great Asset category. Pings will be issued on major events."""
        assert isinstance(interaction.user, discord.Member)

        if interaction.user.get_role(GREAT_ASSET_ROLE_ID):
            await interaction.response.send_message("You already have the Great Asset role!", ephemeral=True)
            return

        await interaction.user.add_roles(discord.Object(id=GREAT_ASSET_ROLE_ID))

    @discord.ui.button(
        label="Helldivers 2",
        custom_id="HyruleHelldivers__",
        style=discord.ButtonStyle.red,
        emoji="\U0001f41b",
        row=1,
    )
    async def add_helldivers_role(self, interaction: Interaction, item: discord.ui.Button[Self]) -> None:
        """This role will allow {owner} to ping you to go helldiving with them!"""
        assert isinstance(interaction.user, discord.Member)

        if interaction.user.get_role(HELLDIVERS_2_ROLE_ID):
            await interaction.response.send_message("You already have the Helldivers 2 role!", ephemeral=True)
            return

        await interaction.user.add_roles(discord.Object(id=HELLDIVERS_2_ROLE_ID))


class Hyrule(commands.Cog):
    def __init__(self, bot: Mipha, /) -> None:
        self.bot: Mipha = bot
        self.bot.loop.create_task(self._assign_view())

    async def _assign_view(self) -> None:
        self.view: HyruleRoleAssignmentView = HyruleRoleAssignmentView(self.bot)
        self.bot.add_view(self.view, message_id=ROLE_ASSIGNMENT_MESSAGE_ID)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.guild.id != HYRULE_GUILD_ID:
            return

        if member.bot:
            await member.add_roles(discord.Object(id=929886067178504234))

    @commands.is_owner()
    @commands.guild_only()
    @commands.command()
    async def setup_role_assignments(self, ctx: GuildContext) -> None:
        """This command will quickly update the role assignment message with the in-memory View created in this same extension."""
        channel = ctx.guild.get_channel(ROLE_ASSIGNMENT_CHANNEL_ID)
        assert isinstance(channel, discord.TextChannel)

        message = channel.get_partial_message(ROLE_ASSIGNMENT_MESSAGE_ID)
        content = "Hey, welcome to Hyrule. Here's some information on the roles available:-\n\n"
        for key, value in self.view._descriptions.items():
            content += f"**{key}**: {value}\n"

        await message.edit(
            content=content,
            view=self.view,
        )


async def setup(bot: Mipha) -> None:
    await bot.add_cog(Hyrule(bot))
