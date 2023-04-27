from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord.ext import commands
from typing_extensions import Self

from utilities.context import GuildContext, Interaction
from utilities.ui import MiphaBaseView


HYRULE_GUILD_ID = 705500489248145459
_IRL_FRIEND_SERVER = 174702278673039360
HONDANA_ROLE_ID = 1086537644093231144
KOTKA_ROLE_ID = 1086537709285285901
ROLE_ASSIGNMENT_CHANNEL_ID = 1086540538112647229
ROLE_ASSIGNMENT_MESSAGE_ID = 1086545767356977173


if TYPE_CHECKING:
    from bot import Mipha


class HyruleRoleAssignmentView(MiphaBaseView):
    def __init__(self, bot: Mipha, /) -> None:
        super().__init__(timeout=None)
        self.bot: Mipha = bot

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
        label="Hondana", custom_id="HyruleHondana__", style=discord.ButtonStyle.blurple, emoji="\U0001F4DA", row=0
    )
    async def add_hondana_role(self, interaction: Interaction, item: discord.ui.Button[Self]) -> None:
        assert isinstance(interaction.user, discord.Member)

        if interaction.user.get_role(HONDANA_ROLE_ID):
            await interaction.response.send_message("You already have the Hondana role!", ephemeral=True)
            return

        await interaction.user.add_roles(discord.Object(id=HONDANA_ROLE_ID))

    @discord.ui.button(
        label="Kotka", custom_id="HyruleKotka__", style=discord.ButtonStyle.blurple, emoji="\U00002694\U0000fe0f", row=0
    )
    async def add_kotka_role(self, interaction: Interaction, item: discord.ui.Button[Self]) -> None:
        assert isinstance(interaction.user, discord.Member)

        if interaction.user.get_role(KOTKA_ROLE_ID):
            await interaction.response.send_message("You already have the Kotka role!")
            return

        await interaction.user.add_roles(discord.Object(id=KOTKA_ROLE_ID))


class Hyrule(commands.Cog):
    def __init__(self, bot: Mipha, /) -> None:
        self.bot: Mipha = bot
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
        await message.edit(
            content="Hey, welcome to Hyrule. Please click the following buttons for the relevant roles if you need them!",
            view=self.view,
        )


async def setup(bot: Mipha) -> None:
    await bot.add_cog(Hyrule(bot))
