from typing import TypedDict


class Details(TypedDict):
    host: str
    port: int
    rcon_port: int
    password: str
    ops: list[int]
