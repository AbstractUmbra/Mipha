from __future__ import annotations

import datetime
import pathlib
from enum import Enum
from typing import TYPE_CHECKING, Any, TypedDict

import discord
from discord.ext import commands

from utilities.async_config import Config
from utilities.checks import has_guild_permissions
from utilities.context import Context


if TYPE_CHECKING:
    from bot import Kukiko

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
    def __init__(self, bot: Kukiko) -> None:
        self.bot: Kukiko = bot
        self._config: Config[VoiceStalkingConfig] = Config(pathlib.Path("configs/voice_stalking.json"))

    def _is_safe_channel(self, channel: VocalGuildChannel) -> bool:
        config: VoiceStalkingConfig = self._config.get(channel.guild.id, {})
        if not config:
            return False

        if config["filtered"] is False:
            return True

        excluded_channels: list[int] = config["excluded_channels"]
        if channel in excluded_channels:
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
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ) -> None:
        config: VoiceStalkingConfig = self._config.get(member.guild.id, {})
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
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @stalking.command()
    @commands.guild_only()
    @has_guild_permissions(manage_channels=True)
    async def exclude(self, ctx: Context, channel: discord.VoiceChannel | discord.StageChannel | discord.Object) -> None:
        config = self._config.get(ctx.guild.id, {})  # type: ignore # guarded by decorator
        if not config:
            return

        config["excluded_channels"].append(channel.id)
        await self._config.put(ctx.guild.id, config)  # type: ignore # guarded by decorator


async def setup(bot: Kukiko) -> None:
    await bot.add_cog(VoiceStalking(bot))
