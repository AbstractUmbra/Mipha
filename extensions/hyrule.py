from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from utilities.shared.checks import restricted_guilds
from utilities.shared.formats import random_pastel_colour
from utilities.shared.ui import BaseView

HYRULE_GUILD_ID = 705500489248145459
_IRL_FRIEND_SERVER = 174702278673039360
HONDANA_ROLE_ID = 1086537644093231144
GREAT_ASSET_ROLE_ID = 1189005762790441010
YUREI_ROLE_ID = 1443946612454981684
BOT_BAIT_CHANNEL_ID = 1238074949424386121
HONEYPOT_ROLE_ID = 1297563765436580010
RULES_CHANNEL_ID = 1238076485600935977
RULES_MESSAGE_ID = 1238077226528800779
ROLE_ASSIGNMENT_CHANNEL_ID = 1086540538112647229
ROLE_ASSIGNMENT_MESSAGE_ID = 1086545767356977173

RULE_REGEX: re.Pattern[str] = re.compile(r"(?P<num>\d)\.\s(?P<content>.*)(?:\r|\n)?")
LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from typing import Self

    from bot import Mipha
    from utilities.context import GuildContext, Interaction


class HyruleRoleAssignmentView(BaseView):
    def __init__(self, bot: Mipha, /) -> None:
        super().__init__(timeout=None)
        self.bot: Mipha = bot
        self._descriptions: dict[str, str] = {
            f"{c.emoji} {c.label}": c.callback.callback.__doc__.format(owner=self.bot.owner.display_name)  # pyright: ignore[reportAttributeAccessIssue]
            for c in self.children
            if isinstance(c, discord.ui.Button) and c.callback.callback.__doc__ and c.label  # pyright: ignore[reportAttributeAccessIssue]
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
    async def add_hondana_role(self, interaction: Interaction, _: discord.ui.Button[Self]) -> None:
        """This role will allow you to send messages in the Hondana category. Pings will be issued on major events."""
        assert isinstance(interaction.user, discord.Member)

        if interaction.user.get_role(HONDANA_ROLE_ID):
            await interaction.response.send_message("You already have the Hondana role!", ephemeral=True)
            return

        await interaction.user.add_roles(discord.Object(id=HONDANA_ROLE_ID))

    @discord.ui.button(
        label="Yurei",
        custom_id="HyruleYurei__",
        style=discord.ButtonStyle.blurple,
        emoji="\U0001f480",
        row=0,
    )
    async def add_yurei_role(self, interaction: Interaction, _: discord.ui.Button[Self]) -> None:
        """This role will allow you to send messages in the Yurei category. Pings will be issued on major events."""
        assert isinstance(interaction.user, discord.Member)

        if interaction.user.get_role(YUREI_ROLE_ID):
            await interaction.response.send_message("You already have the Hondana role!", ephemeral=True)
            return

        await interaction.user.add_roles(discord.Object(id=YUREI_ROLE_ID))


class Hyrule(commands.Cog):
    def __init__(self, bot: Mipha, /) -> None:
        self.bot: Mipha = bot
        self.bot.loop.create_task(self._assign_view())
        self.rules: dict[int, str] = {}

    async def _assign_view(self) -> None:
        await self.bot.wait_until_ready()
        self.view: HyruleRoleAssignmentView = HyruleRoleAssignmentView(self.bot)
        self.bot.add_view(self.view, message_id=ROLE_ASSIGNMENT_MESSAGE_ID)

    def _parse_rules(self, content: str, /) -> None:
        matches = list(RULE_REGEX.finditer(content))

        self.rules = {int(m["num"]): m["content"] for m in matches}

    async def _load_rules(self) -> None:
        if self.rules:
            return

        channel = self.bot.get_partial_messageable(RULES_CHANNEL_ID, guild_id=HYRULE_GUILD_ID, type=discord.ChannelType.text)
        partial_message = channel.get_partial_message(RULES_MESSAGE_ID)

        message = await partial_message.fetch()
        rules_content = message.content

        LOGGER.info("Parsed rules: %s", "\n".join(rules_content.split("\n")))

        self._parse_rules(rules_content)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.guild.id != HYRULE_GUILD_ID:
            return

        if member.bot:
            await member.add_roles(discord.Object(id=929886067178504234))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if not message.guild or message.guild.id != HYRULE_GUILD_ID:
            return

        assert isinstance(message.author, discord.Member)

        if message.channel.id == BOT_BAIT_CHANNEL_ID:
            await message.author.ban(delete_message_days=1, reason="Compromised account, fell for the bait.")

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if before.guild.id != HYRULE_GUILD_ID:
            return

        if (not before.flags.completed_onboarding and after.flags.completed_onboarding) and after.get_role(HONEYPOT_ROLE_ID):
            # joined and did role check
            await after.ban(reason="Failed the honeypot check.")

        if after.get_role(HONEYPOT_ROLE_ID) and not before.get_role(HONEYPOT_ROLE_ID):
            await after.ban(reason="Opted into the honeypot ban.")

    @commands.is_owner()
    @restricted_guilds(HYRULE_GUILD_ID)
    @commands.command()
    async def setup_role_assignments(self, ctx: GuildContext) -> None:
        """
        This command will quickly update the role assignment message with the
        in-memory View created in this same extension.
        """
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

    @commands.guild_only()
    @restricted_guilds(HYRULE_GUILD_ID)
    @commands.group(name="rules", aliases=["rule"])
    async def rules_command(self, ctx: GuildContext, rule_num: int) -> None:
        if not self.rules:
            await self._load_rules()

        try:
            rule = self.rules[rule_num]
        except KeyError:
            return await ctx.send(f"Sorry, but we don't have a rule #{rule_num}", delete_after=5)

        embed = discord.Embed(
            title=f"Rule {rule_num}",
            colour=random_pastel_colour(),
            description=rule,
            timestamp=ctx.message.created_at,
        )
        return await ctx.send(embed=embed)


async def setup(bot: Mipha) -> None:
    await bot.add_cog(Hyrule(bot))
