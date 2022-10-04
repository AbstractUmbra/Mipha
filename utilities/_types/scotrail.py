from typing import TypedDict


__all__ = ("ScotrailData",)

ScotrailPromoted = TypedDict("ScotrailPromoted", {"to": int, "from": int})


class ScotrailStation(TypedDict):
    name: str
    extra: str
    crs: str
    lat: str
    lon: str
    promoted: ScotrailPromoted
    is_toc_station: bool


class ScotrailData(TypedDict):
    stations: dict[str, ScotrailStation]
    updated: int  # timestamp
