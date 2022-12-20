from typing import TypedDict


__all__ = (
    "AudioPost",
    "ImagePost",
)


class AudioPost(TypedDict):
    url: str
    title: str
    author: str
    delete: str
    type: str
    size: int


class ImagePost(TypedDict):
    image: str
    delete: str
    type: str
    size: int
