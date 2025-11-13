from __future__ import annotations

import base64
import io
from typing import TYPE_CHECKING, NamedTuple, Self

import discord
import mcstatus

if TYPE_CHECKING:
    from collections.abc import Generator, Iterable

    from mcstatus.responses import JavaStatusPlayers, JavaStatusResponse

    from ._types.config import Details

__all__ = ("StatusHandler",)


class MCColour(NamedTuple):
    code: str
    name: str
    id: str
    foreground: str
    background: str


MC_COLOR_CODE = "§"
COLORS = [
    MCColour("0", "Black", "black", "000000", "000000"),
    MCColour("1", "Dark Blue", "dark_blue", "0000AA", "00002A"),
    MCColour("2", "Dark Green", "dark_green", "00AA00", "002A00"),
    MCColour("3", "Dark Aqua", "dark_aqua", "00AAAA", "002A2A"),
    MCColour("4", "Dark Red", "dark_red", "AA0000", "2A0000"),
    MCColour("5", "Dark Purple", "dark_purple", "AA00AA", "2A002A"),
    MCColour("6", "Gold", "gold", "FFAA00", "2A2A00"),
    MCColour("7", "Gray", "gray", "AAAAAA", "2A2A2A"),
    MCColour("8", "Dark Gray", "dark_gray", "555555", "151515"),
    MCColour("9", "Blue", "blue", "5555FF", "15153F"),
    MCColour("a", "Green", "green", "55FF55", "153F15"),
    MCColour("b", "Aqua", "aqua", "55FFFF", "153F3F"),
    MCColour("c", "Red", "red", "FF5555", "3F1515"),
    MCColour("d", "Light Purple", "light_purple", "FF55FF", "3F153F"),
    MCColour("e", "Yellow", "yellow", "FFFF55", "3F3F15"),
    MCColour("f", "White", "white", "FFFFFF", "3F3F3F"),
]
COLOR_CODES = {c.code: c for c in COLORS}
CONTROL_CODES = {
    "l": "bold",
    "m": "strikethrough",
    "n": "underline",
    "o": "italic",
    "r": "reset",
}


class StringView:
    def __init__(self, buffer: str) -> None:
        self.index = 0
        self.buffer = buffer
        self.end = len(buffer)

    def take_until(self, char: str, *, eat: bool = True) -> str:
        start = self.index
        while not self.eof():
            if self.buffer[self.index] == char:
                break
            self.index += 1
        res = self.buffer[start : self.index]
        if eat:
            self.index += 1
        return res

    def eof(self) -> bool:
        return self.index >= self.end


class MCSegment:
    __slots__ = ("bold", "color", "italic", "strikethrough", "text", "underline")

    def __init__(
        self,
        text: str,
        color: MCColour | None = None,
        *,
        bold: bool = False,
        strikethrough: bool = False,
        underline: bool = False,
        italic: bool = False,
    ) -> None:
        self.text = text
        self.color = color
        self.bold = bold
        self.italic = italic
        self.strikethrough = strikethrough
        self.underline = underline

    def render_discord(self) -> str:
        if not self.text:
            return ""
        fmt = "{}"
        if self.bold:
            fmt = f"**{fmt}**"
        if self.italic:
            fmt = f"*{fmt}*"
        if self.strikethrough:
            fmt = f"~~{fmt}~~"
        if self.underline:
            fmt = f"__{fmt}__"
        return fmt.format(self.text)

    def __repr__(self) -> str:
        return "<MCSegment " + " ".join(f"{attr}={getattr(self, attr)!r}" for attr in self.__slots__) + ">"


class MCDescription:
    def __init__(self, segments: Iterable[MCSegment]) -> None:
        self.segments = segments

    @classmethod
    def from_text(cls, text: str) -> Self:
        segments = str_to_segments(text)
        return cls(segments)

    @property
    def plain_text(self) -> str:
        return "".join(s.text for s in self.segments)

    @property
    def discord_text(self) -> str:
        return "\u200b".join(s.render_discord() for s in self.segments)


def str_to_segments(buf: str) -> Generator[MCSegment]:
    view = StringView(buf)

    status = {}
    first_segment = view.take_until(MC_COLOR_CODE)
    yield MCSegment(text=first_segment)

    while not view.eof():
        seg_raw = view.take_until(MC_COLOR_CODE)
        code, text = seg_raw[0], seg_raw[1:]
        if code in COLOR_CODES:
            status["color"] = COLOR_CODES[code].id
            yield MCSegment(text=text, **status)
        elif code in CONTROL_CODES:
            if code == "r":
                status = {}
            else:
                status[CONTROL_CODES[code]] = True
            yield MCSegment(text=text, **status)


class StatusHandler:
    def __init__(self, config: dict[str, Details], /) -> None:
        self.config = config

    async def server_status(
        self, *, server_config: Details | None = None, server_string: str | None = None
    ) -> JavaStatusResponse:
        if not server_config and not server_string:
            raise ValueError("One of `server_config` or `server_string` must be provided.")

        if server_config:
            server = mcstatus.JavaServer(host=server_config["host"], port=server_config["port"])
        else:
            assert server_string
            host, _, port = server_string.partition(":")
            server = mcstatus.JavaServer(host=host, port=int(port))

        return await server.async_status()

    async def players(self, server_config: Details) -> JavaStatusPlayers:
        server = mcstatus.JavaServer(host=server_config["host"], port=server_config["port"])
        status = await server.async_status()
        return status.players

    def mcstatus_message(self, status: JavaStatusResponse) -> tuple[discord.Embed, discord.File | None]:
        status.description = MCDescription.from_text(status.description)  # pyright: ignore[reportAttributeAccessIssue]

        embed = discord.Embed()

        file = None
        if status.icon:
            _, b64 = status.icon.split(",")
            file = discord.File(io.BytesIO(base64.b64decode(b64)), filename="icon.png")
            embed.set_thumbnail(url="attachment://icon.png")

        embed.description = "\u200b" + status.description.discord_text

        embed.add_field(name="version", value=f"{status.version.name} (proto {status.version.protocol})    \u200b")
        embed.add_field(name="ping", value=status.latency)
        players_string = f"{status.players.online}/{status.players.max}    \u200b"
        if status.players.sample:
            players_string += "".join(f"\n[{p.name}]({p.id})" for p in status.players.sample)
        embed.add_field(name="players", value=players_string)

        return (embed, file)
