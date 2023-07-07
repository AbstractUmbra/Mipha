from __future__ import annotations

from discord.enums import Enum


class CoupCards(Enum):
    duke = 1
    assassin = 2
    contessa = 3
    captain = 4
    ambassador = 5


class CoupAction(Enum):
    income = 1
    foreign_aid = 2
    coup = 3
    tax = 4
    assassinate = 5
    steal = 6
    exchange = 7


class CoupBlock(Enum):
    foreign_aid = 1
    assassination = 2
    stealing = 3


_card_to_action_mapping = {
    CoupCards.duke: CoupAction.tax,
    CoupCards.assassin: CoupAction.assassinate,
    CoupCards.captain: CoupAction.steal,
    CoupCards.ambassador: CoupAction.exchange,
}

_card_to_block_mapping = {
    CoupCards.duke: CoupBlock.foreign_aid,
    CoupCards.contessa: CoupBlock.assassination,
    CoupCards.captain: CoupBlock.stealing,
    CoupCards.ambassador: CoupBlock.stealing,
}


class Card:
    def __init__(self, type: CoupCards, /) -> None:
        self.type: CoupCards = type


class Player:
    def __init__(self, *, cards: tuple[Card, Card], id_: int) -> None:
        self.tokens: int = 0
        self._cards: tuple[Card, Card] = cards
        self.id_: int = id_

    @property
    def cards(self) -> tuple[Card, Card]:
        return self._cards

    @cards.setter
    def cards(self, other: tuple[Card, Card]) -> None:
        self._cards = other

    def available_actions(self) -> list[CoupAction]:
        if self.tokens >= 7:
            return [CoupAction.coup]

        base: list[CoupAction] = [CoupAction.income, CoupAction.foreign_aid]
        for card in self.cards:
            action = _card_to_action_mapping.get(card.type)
            if action:
                base.append(action)

        return base

    def available_blocks(self) -> list[CoupBlock]:
        base: list[CoupBlock] = []
        for card in self.cards:
            block = _card_to_block_mapping.get(card.type)
            if block:
                base.append(block)

        return base


class CoupGame:
    def __init__(self, *, players: list[Player], guild_id: int, channel_id: int) -> None:
        self.players: list[Player] = players
        self.guild_id: int = guild_id
        self.channel_id: int = channel_id
