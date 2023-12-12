from __future__ import annotations

import datetime
import pathlib
from enum import Enum
from typing import TYPE_CHECKING, Any, TypedDict

import discord
from discord.ext import commands

from utilities.shared.async_config import Config
from utilities.shared.checks import has_guild_permissions

if TYPE_CHECKING:
    from bot import Mipha
    from utilities.context import Context, GuildContext

    VocalGuildChannel = discord.VoiceChannel | discord.StageChannel


class BadStalkingConfig(commands.UserInputError):
    def __init__(self, guild: discord.Guild, *args: Any) -> None:
        message = f"Bad config in {guild} ({guild.id}) for voice stalking."
        super().__init__(message, *args)


class VoiceStateType(Enum):
    connect = 0
    disconnect = 1
    move = 2


class VoiceStalkingConfig(TypedDict):
    notification_channel: int
    excluded_channels: list[int]
    filtered: bool


COLOUR_LOOKUP: dict[VoiceStateType, discord.Colour] = {
    VoiceStateType.connect: discord.Colour.green(),
    VoiceStateType.disconnect: discord.Colour.red(),
    VoiceStateType.move: discord.Colour.blurple(),
}
NORMALIZE_STATE: dict[VoiceStateType, str] = {
    VoiceStateType.connect: "joined",
    VoiceStateType.disconnect: "left",
    VoiceStateType.move: "moved",
}


class VoiceStalking(commands.Cog):
    def __init__(self, bot: Mipha) -> None:
        self.bot: Mipha = bot
        self._config: Config[VoiceStalkingConfig] = Config(pathlib.Path("configs/voice_stalking.json"))

    async def cog_check(self, ctx: Context) -> bool:
        return await ctx.bot.is_owner(ctx.author)

    def _create_default_config(self) -> VoiceStalkingConfig:
        ret: VoiceStalkingConfig = {"notification_channel": 0, "excluded_channels": [], "filtered": False}

        return ret

    def _is_safe_channel(self, channel: VocalGuildChannel) -> bool:
        config: VoiceStalkingConfig = self._config.get(channel.guild.id, {})  # type: ignore # dumb
        if not config:
            return False

        if config["filtered"] is False:
            return True

        excluded_channels: list[int] = config["excluded_channels"]
        if channel.id in excluded_channels:
            return False

        default_role = channel.guild.default_role
        if channel.permissions_for(default_role).view_channel is False:
            return False

        return True

    def generate_embed(
        self,
        member: discord.Member,
        /,
        *,
        state: VoiceStateType,
        before: VocalGuildChannel | None,
        after: VocalGuildChannel | None,
    ) -> discord.Embed:
        embed = discord.Embed(colour=COLOUR_LOOKUP[state])
        embed.set_author(name=member.name, icon_url=member.display_avatar.url)

        normalised = NORMALIZE_STATE[state]
        if state is VoiceStateType.connect:
            assert after is not None
            fmt = f"{member.mention} has {normalised} {after.mention}."
        elif state is VoiceStateType.disconnect:
            assert before is not None
            fmt = f"{member.mention} has {normalised} {before.mention}."
        elif state is VoiceStateType.move:
            assert before is not None
            assert after is not None
            fmt = f"{member.mention} has {normalised} from {before.mention} to {after.mention}."

        embed.description = fmt
        embed.timestamp = datetime.datetime.now(datetime.timezone.utc)

        return embed

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        config: VoiceStalkingConfig = self._config.get(member.guild.id, {})  # type: ignore # dumb
        if not config:
            return

        if before.channel is None and after.channel is not None:
            state = VoiceStateType.connect
        elif before.channel and after.channel:
            if before.channel == after.channel:
                return
            state = VoiceStateType.move
        elif before.channel is not None and after.channel is None:
            state = VoiceStateType.disconnect
        else:
            raise RuntimeError("Unreachable code in voice stalking.")

        channel_id = config["notification_channel"]
        channel: discord.TextChannel | None = self.bot.get_channel(channel_id)  # type: ignore # only text channels will be allowed.
        if channel is None:
            raise BadStalkingConfig(member.guild)

        if state is VoiceStateType.move:
            if self._is_safe_channel(before.channel) and self._is_safe_channel(after.channel):  # type: ignore # this is guarded by the state above
                state = VoiceStateType.move
            elif self._is_safe_channel(before.channel) and not self._is_safe_channel(after.channel):  # type: ignore # this is guarded by the state above
                state = VoiceStateType.disconnect
            elif not self._is_safe_channel(before.channel) and self._is_safe_channel(after.channel):  # type: ignore # this is guarded by the state above
                state = VoiceStateType.connect
        if state is VoiceStateType.connect and not self._is_safe_channel(after.channel):  # type: ignore # this is guarded by the state above
            return
        if state is VoiceStateType.disconnect and not self._is_safe_channel(before.channel):  # type: ignore # this is guarded by the state above
            return

        embed = self.generate_embed(member, state=state, before=before.channel, after=after.channel)
        await channel.send(embed=embed)

    @commands.group()
    async def stalking(self, ctx: Context) -> None:
        """Voice stalking parent command."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @stalking.command()
    @commands.guild_only()
    async def setup(self, ctx: GuildContext) -> None:
        """Set up this guild for voice stalking."""
        config = self._config.get(ctx.guild.id)
        if config:
            return await ctx.send("It seems it's already set up here?")

        config = self._create_default_config()
        await self._config.put(ctx.guild.id, config)

        await ctx.message.add_reaction(ctx.tick(True))

    @stalking.command()
    @commands.guild_only()
    @has_guild_permissions(manage_channels=True)
    async def exclude(
        self,
        ctx: GuildContext,
        channel: discord.VoiceChannel | discord.StageChannel | discord.Object,
    ) -> None:
        """Exclude a channel from the voice stalking."""
        config = self._config.get(ctx.guild.id)
        if not config:
            return

        config["excluded_channels"].append(channel.id)
        await self._config.put(ctx.guild.id, config)

    @stalking.command()
    @commands.guild_only()
    @has_guild_permissions(manage_channels=True)
    async def create(self, ctx: GuildContext, name: str, exclude: commands.Greedy[discord.Member | discord.Role]) -> None:
        """Create an excluded text and voice channel combination."""
        config: VoiceStalkingConfig | None = self._config.get(ctx.guild.id)
        if not config:
            return

        no_perms_overwrite = discord.PermissionOverwrite.from_pair(discord.Permissions.none(), discord.Permissions.all())
        overwrites = {item: no_perms_overwrite for item in exclude}

        tc = await ctx.guild.create_text_channel(name=name, overwrites=overwrites)
        vc = await ctx.guild.create_voice_channel(name=name, overwrites=overwrites)

        config["excluded_channels"].append(vc.id)
        await self._config.put(ctx.guild.id, config)

        try:
            await ctx.author.send(
                f"Okay, I have created {tc.mention} and {vc.mention} for you, and excluded the following:-\n\n"
                + "\n".join([i.mention for i in exclude]),
            )
        except discord.HTTPException:
            pass


async def setup(bot: Mipha) -> None:
    await bot.add_cog(VoiceStalking(bot))
