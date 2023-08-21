from typing import TypedDict

__all__ = ("GelbooruPayload",)


class GelbooruPostPayload(TypedDict):
    id: int
    created_at: str  # weird datetime
    score: int
    width: int
    height: int
    md5: str
    directory: str
    image: str
    rating: str
    source: str
    change: int
    owner: str
    creator_id: int
    parent_id: int
    sample: int
    preview_height: int
    preview_width: int
    tags: str
    title: str
    has_notes: str  # bool?
    has_comments: str  # bool?
    file_url: str
    preview_url: str
    sample_url: str
    sample_height: int
    sample_width: int
    status: str
    post_locked: int
    has_children: str  # bool?


class GelbooruPagination(TypedDict):
    limit: int
    offset: int
    count: int


GelbooruPayload = TypedDict("GelbooruPayload", {"@attributes": GelbooruPagination, "post": list[GelbooruPostPayload]})
