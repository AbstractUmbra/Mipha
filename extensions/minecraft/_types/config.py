from typing import NotRequired, TypedDict

__all__ = ("Details",)


class BackupDetails(TypedDict):
    command: str
    args: list[str]


class Details(TypedDict):
    host: str
    port: int
    rcon_port: int
    password: str
    ops: list[int]
    backup: NotRequired[BackupDetails]
