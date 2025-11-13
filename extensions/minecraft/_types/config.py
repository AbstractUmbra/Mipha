from typing import TypedDict


class Details(TypedDict):
    host: str
    port: int
    password: str
    ops: list[int]
