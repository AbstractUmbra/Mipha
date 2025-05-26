from __future__ import annotations

from typing import TYPE_CHECKING

from discord import app_commands
from discord.enums import Enum
from discord.ext import commands

if TYPE_CHECKING:
    from bot import Mipha
    from utilities.context import Interaction


class PokeType(Enum):
    Normal = 0
    Fire = 1
    Water = 2
    Electric = 3
    Grass = 4
    Ice = 5
    Fighting = 6
    Poison = 7
    Ground = 8
    Flying = 9
    Psychic = 10
    Bug = 11
    Rock = 12
    Ghost = 13
    Dragon = 14
    Dark = 15
    Steel = 16
    Fairy = 17

    def in_gen(self, generation: int) -> bool:
        return not (
            (generation == 1 and self in (PokeType.Dark, PokeType.Steel, PokeType.Fairy))
            or (2 <= generation <= 4 and self is PokeType.Fairy)
        )


class Effectiveness(Enum):
    INEFFECTIVE = 0
    NOT_EFFECTIVE = 1
    STANDARD = 2
    SUPER_EFFECTIVE = 3
    DOUBLE_SUPER_EFFECTIVE = 99

    def __gt__(self, other: Effectiveness) -> bool:
        return self.value > other.value

    def __lt__(self, other: Effectiveness) -> bool:
        return not self.__gt__(other)


POKEMON_TYPE_LIST = list(PokeType)

GEN_1_RESOLUTIONS = [
    # normal
    [2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 1, 0, 2],
    # fire
    [2, 1, 1, 2, 3, 3, 2, 2, 2, 2, 2, 3, 1, 2, 1],
    # water
    [2, 3, 1, 2, 1, 2, 2, 2, 3, 2, 2, 2, 3, 2, 1],
    # electric
    [2, 2, 3, 1, 1, 2, 2, 2, 0, 3, 2, 2, 2, 2, 1],
    # grass
    [2, 1, 3, 2, 1, 2, 2, 1, 3, 1, 2, 1, 3, 2, 1],
    # ice
    [2, 2, 1, 2, 3, 1, 2, 2, 3, 3, 2, 2, 2, 2, 3],
    # fighting
    [3, 2, 2, 2, 2, 3, 2, 1, 2, 1, 1, 1, 2, 0, 2],
    # poison
    [2, 2, 2, 2, 3, 2, 2, 1, 1, 2, 2, 3, 1, 1, 2],
    # ground
    [2, 3, 2, 3, 1, 2, 2, 3, 2, 0, 2, 1, 3, 2, 2],
    # flying
    [2, 2, 2, 1, 3, 2, 3, 2, 2, 2, 2, 3, 1, 2, 2],
    # psychic
    [2, 2, 2, 2, 2, 2, 3, 3, 2, 2, 1, 2, 2, 2, 2],
    # bug
    [2, 1, 2, 2, 3, 2, 1, 3, 2, 1, 3, 2, 2, 1, 2],
    # rock
    [2, 3, 2, 2, 2, 3, 1, 2, 1, 3, 2, 3, 2, 2, 2],
    # ghost
    [0, 2, 2, 2, 2, 2, 2, 2, 2, 2, 0, 2, 2, 3, 2],
    # dragon
    [2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 3],
]

GEN_2_5_RESOLUTIONS = [
    # normal
    [2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 1, 0, 2, 2, 0],
    # fire
    [2, 1, 1, 2, 3, 3, 2, 2, 2, 2, 2, 3, 1, 2, 1, 2, 3],
    # water
    [2, 3, 1, 2, 1, 2, 2, 2, 3, 2, 2, 2, 3, 2, 1, 2, 2],
    # electric
    [2, 2, 3, 1, 1, 2, 2, 2, 0, 3, 2, 2, 2, 2, 1, 2, 2],
    # grass
    [2, 1, 3, 2, 1, 2, 2, 1, 3, 1, 2, 1, 3, 2, 1, 2, 1],
    # ice
    [2, 1, 1, 2, 3, 1, 2, 2, 3, 3, 2, 2, 2, 2, 3, 2, 1],
    # fighting
    [3, 2, 2, 2, 2, 3, 2, 1, 2, 1, 1, 1, 3, 0, 2, 3, 3],
    # poison
    [2, 2, 2, 2, 3, 2, 2, 1, 1, 2, 2, 2, 1, 1, 2, 2, 0],
    # ground
    [2, 3, 2, 3, 1, 2, 2, 3, 2, 0, 2, 1, 3, 2, 2, 2, 3],
    # flying
    [2, 2, 2, 1, 3, 2, 3, 2, 2, 2, 2, 3, 1, 2, 2, 2, 1],
    # psychic
    [2, 2, 2, 2, 2, 2, 3, 3, 2, 2, 1, 2, 2, 2, 2, 0, 1],
    # bug
    [2, 1, 2, 2, 3, 2, 1, 1, 2, 1, 3, 2, 2, 1, 2, 3, 1],
    # rock
    [2, 3, 2, 2, 2, 3, 1, 2, 1, 3, 2, 3, 2, 2, 2, 2, 1],
    # ghost
    [0, 2, 2, 2, 2, 2, 2, 2, 2, 2, 3, 2, 2, 3, 2, 1, 1],
    # dragon
    [2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 3, 2, 1],
    # dark
    [2, 2, 2, 2, 2, 2, 1, 2, 2, 2, 3, 2, 2, 3, 2, 1, 1],
    # steel
    [2, 1, 1, 1, 2, 3, 2, 2, 2, 2, 2, 2, 3, 2, 2, 2, 1],
]

GEN_6_PLUS_RESOLUTIONS = [
    # normal
    [2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 1, 0, 2, 2, 0, 2],
    # fire
    [2, 1, 1, 2, 3, 3, 2, 2, 2, 2, 2, 3, 1, 2, 1, 2, 3, 2],
    # water
    [2, 3, 1, 2, 1, 2, 2, 2, 3, 2, 2, 2, 3, 2, 1, 2, 2, 2],
    # electric
    [2, 2, 3, 1, 1, 2, 2, 2, 0, 3, 2, 2, 2, 2, 1, 2, 2, 2],
    # grass
    [2, 1, 3, 2, 1, 2, 2, 1, 3, 1, 2, 1, 3, 2, 1, 2, 1, 2],
    # ice
    [2, 1, 1, 2, 3, 1, 2, 2, 3, 3, 2, 2, 2, 2, 3, 2, 1, 2],
    # fighting
    [3, 2, 2, 2, 2, 3, 2, 1, 2, 1, 1, 1, 3, 0, 2, 3, 3, 1],
    # poison
    [2, 2, 2, 2, 3, 2, 2, 1, 1, 2, 2, 2, 1, 1, 2, 2, 0, 3],
    # ground
    [2, 3, 2, 3, 1, 2, 2, 3, 2, 0, 2, 1, 3, 2, 2, 2, 3, 2],
    # flying
    [2, 2, 2, 1, 3, 2, 3, 2, 2, 2, 2, 3, 1, 2, 2, 2, 1, 2],
    # psychic
    [2, 2, 2, 2, 2, 2, 3, 3, 2, 2, 1, 2, 2, 2, 2, 0, 1, 2],
    # bug
    [2, 1, 2, 2, 3, 2, 1, 1, 2, 1, 3, 2, 2, 1, 2, 3, 1, 1],
    # rock
    [2, 3, 2, 2, 2, 3, 1, 2, 1, 3, 2, 3, 2, 2, 2, 2, 1, 2],
    # ghost
    [0, 2, 2, 2, 2, 2, 2, 2, 2, 2, 3, 2, 2, 3, 2, 1, 2, 2],
    # dragon
    [2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 3, 2, 1, 0],
    # dark
    [2, 2, 2, 2, 2, 2, 1, 2, 2, 2, 3, 2, 2, 3, 2, 1, 2, 1],
    # steel
    [2, 1, 1, 1, 2, 3, 2, 2, 2, 2, 2, 2, 3, 2, 2, 2, 1, 3],
    # fairy
    [2, 1, 2, 2, 2, 2, 3, 1, 2, 2, 2, 2, 2, 2, 3, 3, 1, 2],
]

DAMAGE_MULTIPLIER = {
    Effectiveness.INEFFECTIVE: 0,
    Effectiveness.NOT_EFFECTIVE: 0.5,
    Effectiveness.STANDARD: 1,
    Effectiveness.SUPER_EFFECTIVE: 2,
    Effectiveness.DOUBLE_SUPER_EFFECTIVE: 4,
}


class Pokemon(commands.GroupCog):
    def __init__(self, bot: Mipha, /) -> None:
        self.bot = bot

    def _is_in_gen(
        self, attack: PokeType, defender_one: PokeType, defender_two: PokeType | None, *, generation: int
    ) -> bool:
        if not attack.in_gen(generation):
            return False

        if not defender_one.in_gen(generation):
            return False

        return bool(defender_two and defender_two.in_gen(generation))

    def _resolve_types(
        self, generation: int, /, *, attack: PokeType, defender_one: PokeType, defender_two: PokeType | None
    ) -> Effectiveness:
        if generation == 1:
            resolver = GEN_1_RESOLUTIONS
        elif 2 <= generation <= 5:
            resolver = GEN_2_5_RESOLUTIONS
        elif 6 <= generation <= 9:
            resolver = GEN_6_PLUS_RESOLUTIONS
        else:
            raise ValueError("Unknown generation specified.")

        if not self._is_in_gen(attack, defender_one, defender_two, generation=generation):
            raise ValueError("Out of gen attack or defense types provided")

        type_two_effectiveness: Effectiveness | None = None
        type_one_effectiveness = Effectiveness(
            resolver[POKEMON_TYPE_LIST.index(attack)][POKEMON_TYPE_LIST.index(defender_one)]
        )
        if defender_two:
            type_two_effectiveness = Effectiveness(
                resolver[POKEMON_TYPE_LIST.index(attack)][POKEMON_TYPE_LIST.index(defender_two)]
            )

        if type_one_effectiveness is Effectiveness.INEFFECTIVE or type_two_effectiveness is Effectiveness.INEFFECTIVE:
            return Effectiveness.INEFFECTIVE

        if (
            type_one_effectiveness is Effectiveness.SUPER_EFFECTIVE
            and type_two_effectiveness is Effectiveness.SUPER_EFFECTIVE
        ):
            return Effectiveness.DOUBLE_SUPER_EFFECTIVE

        if type_two_effectiveness:
            return max(type_one_effectiveness, type_two_effectiveness)

        return type_one_effectiveness

    @app_commands.command(name="type-calculator")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.describe(
        attack_type="The type of the attack being used.",
        defender_type_one="The target's first type.",
        defender_type_two="The target's second type, if any.",
        generation="The Pokemon generation being played. Defaults to 2.",
    )
    async def type_calculator(
        self,
        interaction: Interaction,
        attack_type: PokeType,
        defender_type_one: PokeType,
        defender_type_two: PokeType | None = None,
        generation: app_commands.Range[int, 1, 9] = 2,
    ) -> None:
        """A Pokémon move type calculator."""

        defender_type = defender_type_one.name + (f"/{defender_type_two.name}" if defender_type_two else "")
        content = f"Chosen {attack_type.name} to attack a {defender_type} type."
        await interaction.response.send_message(content)

        try:
            effectiveness = self._resolve_types(
                generation, attack=attack_type, defender_one=defender_type_one, defender_two=defender_type_two
            )
        except ValueError:
            await interaction.edit_original_response(
                content=(
                    "Sorry, but you've provided bad information."
                    "Are the types you've specified actually part of that generation?"
                )
            )
            return

        dmg_multiplier = DAMAGE_MULTIPLIER[effectiveness]

        await interaction.edit_original_response(content=content + f"\n\nYour attack will be {dmg_multiplier}x effective.")


async def setup(bot: Mipha, /) -> None:
    await bot.add_cog(Pokemon(bot))
