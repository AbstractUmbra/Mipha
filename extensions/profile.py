from __future__ import annotations

import re
from collections import defaultdict
from typing import TYPE_CHECKING, Annotated, Literal

import discord
from discord import app_commands
from discord.ext import commands

from utilities.formats import plural


if TYPE_CHECKING:
    from bot import Mipha
    from utilities.context import Context


class DisambiguateMember(commands.IDConverter, app_commands.Transformer):
    async def convert(self, ctx: Context, argument: str) -> discord.abc.User:
        # check if it's a user ID or mention
        match = self._get_id_match(argument) or re.match(r"<@!?([0-9]+)>$", argument)

        if match is not None:
            # exact matches, like user ID + mention should search
            # for every member we can see rather than just this guild.
            user_id = int(match.group(1))
            result = ctx.bot.get_user(user_id)
            if result is None:
                try:
                    result = await ctx.bot.fetch_user(user_id)
                except discord.HTTPException:
                    raise commands.BadArgument("Could not find this member.") from None
            return result

        # check if we have a discriminator:
        if len(argument) > 5 and argument[-5] == "#":
            # note: the above is true for name#discrim as well
            name, _, discriminator = argument.rpartition("#")
            pred = lambda u: u.name == name and u.discriminator == discriminator
            result = discord.utils.find(pred, ctx.bot.users)
        else:
            matches: list[discord.Member | discord.User]
            # disambiguate I guess
            if ctx.guild is None:
                matches = [user for user in ctx.bot.users if user.name == argument]
                entry = str
            else:
                matches = [
                    member
                    for member in ctx.guild.members
                    if member.name == argument or (member.nick and member.nick == argument)
                ]

                def to_str(m):
                    if m.nick:
                        return f"{m} (a.k.a {m.nick})"
                    else:
                        return str(m)

                entry = to_str

            try:
                result = await ctx.disambiguate(matches, entry)
            except Exception as e:
                raise commands.BadArgument(f"Could not find this member. {e}") from None

        if result is None:
            raise commands.BadArgument("Could not find this member. Note this is case sensitive.")
        return result

    @property
    def type(self) -> discord.AppCommandOptionType:
        return discord.AppCommandOptionType.user

    async def transform(self, interaction: discord.Interaction, value: discord.abc.User) -> discord.abc.User:
        return value


def valid_nnid(argument: str) -> str:
    arg = argument.strip('"')
    if len(arg) > 16:
        raise commands.BadArgument("An NNID has a maximum of 16 characters.")
    return arg


_friend_code = re.compile(r"^(?:(?:SW|3DS)[- _]?)?(?P<one>[0-9]{4})[- _]?(?P<two>[0-9]{4})[- _]?(?P<three>[0-9]{4})$")


def valid_fc(argument: str, *, _fc=_friend_code) -> str:
    fc = argument.upper().strip('"')
    m = _fc.match(fc)
    if m is None:
        raise commands.BadArgument("Not a valid friend code!")

    return "{one}-{two}-{three}".format(**m.groupdict())


class ProfileCreateModal(discord.ui.Modal, title="Create Profile"):
    switch = discord.ui.TextInput(label="Switch Friend Code", placeholder="1234-5678-9012")
    three_ds = discord.ui.TextInput(label="3DS Friend Code", placeholder="1234-5678-9012")
    wii_u = discord.ui.TextInput(label="Wii U Friend Code", placeholder="1234-5678-9012")

    def __init__(self, cog: Profile, ctx: Context) -> None:
        super().__init__()
        self.cog: Profile = cog
        self.ctx: Context = ctx

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        extra = {}
        try:
            fc_switch = valid_fc(str(self.switch.value))
            fc_wii_u = valid_nnid(str(self.wii_u.value))
            fc_three_ds = valid_fc(str(self.three_ds.value))

        except commands.BadArgument as e:
            await interaction.followup.send(f"Sorry, an error happened while setting up your profile:\n{e}", ephemeral=True)
            return

        query = """
            INSERT INTO profiles (id, fc_switch, fc_3ds, nnid, extra)
            VALUES ($1, $2, $3, $4, $5::jsonb)
            ON CONFLICT (id)
            DO UPDATE
            SET fc_switch = profiles.fc_switch || EXCLUDED.fc_switch,
                fc_3ds =  profiles.fc_3ds || EXCLUDED.fc_3ds,
                nnid = profiles.nnid || EXCLUDED.nnid,
                extra = profiles.extra || EXCLUDED.extra;
        """

        try:
            await self.ctx.db.execute(query, self.ctx.author.id, fc_switch, fc_three_ds, fc_wii_u, extra)
        except Exception as e:
            await interaction.followup.send(f"Sorry, an error happened while setting up your profile:\n{e}", ephemeral=True)
        else:
            await interaction.followup.send("Successfully created your profile", ephemeral=True)


class PromptProfileCreationView(discord.ui.View):
    def __init__(self, cog: Profile, ctx: Context) -> None:
        super().__init__()
        self.cog: Profile = cog
        self.ctx: Context = ctx

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("Sorry, this button is not meant for you.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Create Profile", style=discord.ButtonStyle.blurple)
    async def create_profile(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ProfileCreateModal(self.cog, self.ctx))


class Profile(commands.Cog):
    """Manage your Nintendo profiles."""

    def __init__(self, bot: Mipha) -> None:
        self.bot: Mipha = bot

    async def cog_command_error(self, ctx: Context, error: commands.CommandError):
        if isinstance(error, commands.BadArgument):
            await ctx.send(str(error), ephemeral=True)

    @commands.hybrid_group(invoke_without_command=True, fallback="get")
    @app_commands.describe(member="The member profile to get, if not given then it shows your profile")
    async def profile(
        self,
        ctx: Context,
        *,
        member: discord.Member | discord.User = commands.param(converter=DisambiguateMember, default=None),
    ):
        """Retrieves a member's profile.

        All commands will create a profile for you.
        """

        member = member or ctx.author

        query = """SELECT * FROM profiles WHERE id=$1;"""
        record = await ctx.db.fetchrow(query, member.id)

        if record is None:
            if member == ctx.author:
                await ctx.send(
                    "You did not set up a profile. Press the button below to set one up.",
                    view=PromptProfileCreationView(self, ctx),
                )
            else:
                await ctx.send("This member did not set up a profile.")
            return

        e = discord.Embed(colour=discord.Colour.random())

        keys = {
            "fc_switch": "Switch FC",
            "nnid": "Wii U NNID",
            "fc_3ds": "3DS FC",
        }

        for key, value in keys.items():
            e.add_field(name=value, value=record[key] or "N/A", inline=True)

        # consoles = [f'__{v}__: {record[k]}' for k, v in keys.items() if record[k] is not None]
        # e.add_field(name='Consoles', value='\n'.join(consoles) if consoles else 'None!', inline=False)
        e.set_author(name=member.display_name, icon_url=member.display_avatar.with_format("png"))
        await ctx.send(embed=e)

    async def edit_fields(self, ctx: Context, **fields: str):
        keys = ", ".join(fields)
        values = ", ".join(f"${2 + i}" for i in range(len(fields)))

        query = f"""INSERT INTO profiles (id, {keys})
                    VALUES ($1, {values})
                    ON CONFLICT (id)
                    DO UPDATE
                    SET ({keys}) = ROW({values});
                 """

        await ctx.db.execute(query, ctx.author.id, *fields.values())

    @profile.command(usage="<NNID>")
    @app_commands.describe(nnid="Your NNID (Nintendo Network ID)")
    async def nnid(self, ctx: Context, *, nnid: Annotated[str, valid_nnid]):
        """Sets the NNID portion of your profile."""
        await self.edit_fields(ctx, nnid=nnid)
        await ctx.send("Updated NNID.")

    @profile.command(name="3ds")
    @app_commands.describe(fc="Your 3DS Friend Code")
    async def profile_3ds(self, ctx: Context, *, fc: Annotated[str, valid_fc]):
        """Sets the 3DS friend code of your profile."""
        await self.edit_fields(ctx, fc_3ds=fc)
        await ctx.send("Updated 3DS friend code.")

    @profile.command()
    @app_commands.describe(fc="Your Switch Friend Code")
    async def switch(self, ctx: Context, *, fc: Annotated[str, valid_fc]):
        """Sets the Switch friend code of your profile."""
        await self.edit_fields(ctx, fc_switch=fc)
        await ctx.send("Updated Switch friend code.")

    @profile.command()
    @app_commands.choices(
        field=[
            app_commands.Choice(value="all", name="Everything"),
            app_commands.Choice(value="nnid", name="NNID"),
            app_commands.Choice(value="switch", name="Switch Friend Code"),
            app_commands.Choice(value="3ds", name="3DS Friend Code"),
        ]
    )
    @app_commands.describe(field="The field to delete from your profile. If not given then your entire profile is deleted.")
    async def delete(
        self,
        ctx: Context,
        *,
        field: Literal[
            "all",
            "nnid",
            "switch",
            "3ds",
        ] = "all",
    ) -> None:
        """Deletes a field from your profile.

        The valid fields that could be deleted are:

        - all
        - nnid
        - switch
        - 3ds
        - squad
        - weapon
        - rank

        Omitting a field will delete your entire profile.
        """

        # simple case: delete entire profile
        if field == "all":
            confirm = await ctx.prompt("Are you sure you want to delete your profile?")
            if confirm:
                query = "DELETE FROM profiles WHERE id=$1;"
                await ctx.db.execute(query, ctx.author.id)
                await ctx.send("Successfully deleted profile.")
            else:
                await ctx.send("Aborting profile deletion.")
            return

        # a little intermediate case, basic field deletion:
        field_to_column = {
            "nnid": "nnid",
            "switch": "fc_switch",
            "3ds": "fc_3ds",
        }

        column = field_to_column.get(field)
        if column:
            query = f"UPDATE profiles SET {column} = NULL WHERE id=$1;"
            await ctx.db.execute(query, ctx.author.id)
            await ctx.send(f"Successfully deleted {field} field.")
            return

    @profile.command()
    @app_commands.describe(query="The search query, must be at least 3 characters")
    async def search(self, ctx: Context, *, query: str):
        """Searches profiles via either friend code or NNID.

        The query must be at least 3 characters long.

        Results are returned matching whichever criteria is met.
        """

        # check if it's a valid friend code and search the database for it:

        try:
            value = valid_fc(query.upper())
        except:
            # invalid so let's search for NNID/Squad.
            value = query
            query = """SELECT format('<@%s>', id) AS "User", fc_switch AS "Switch", nnid AS "NNID"
                       FROM profiles
                       WHERE nnid ILIKE '%' || $1 || '%'
                       LIMIT 15;
                    """
        else:
            query = """SELECT format('<@%s>', id) AS "User", fc_switch AS "Switch", fc_3ds AS "3DS"
                       FROM profiles
                       WHERE fc_switch=$1 OR fc_3ds=$1
                       LIMIT 15;
                    """

        records = await ctx.db.fetch(query, value)

        if len(records) == 0:
            return await ctx.send("No results found...")

        e = discord.Embed(colour=discord.Colour.random())

        data = defaultdict(list)
        for record in records:
            for key, value in record.items():
                data[key].append(value if value else "N/A")

        for key, value in data.items():
            e.add_field(name=key, value="\n".join(value))

        # a hack to allow multiple inline fields
        e.set_footer(text=format(plural(len(records)), "record") + "\u2003" * 60 + "\u200b")
        await ctx.send(embed=e)


async def setup(bot: Mipha) -> None:
    await bot.add_cog(Profile(bot))
