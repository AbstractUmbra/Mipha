from typing import TypedDict


__all__ = ("DanbooruPayload",)


class DanbooruPayload(TypedDict):
    id: int
    created_at: str  # time format
    uploader_id: int
    score: int
    source: str
    md5: str
    last_comment_bumped_at: str | None  # datetime?
    rating: str
    image_width: int
    image_height: int
    tag_string: str
    fav_count: int
    file_ext: str
    last_noted_at: str | None  # datetime?
    parent_id: int | None
    has_children: bool
    approver_id: int | None
    tag_count_general: int
    tag_count_artist: int
    tag_count_character: int
    tag_count_copyright: int
    file_size: int
    up_score: int
    down_score: int
    is_pending: bool
    is_flagged: bool
    is_deleted: bool
    tag_count: int
    updated_at: str  # datetiume
    is_banned: bool
    pixiv_id: str | None  # ?
    last_commented_at: str | None  # datetime?
    has_active_children: bool
    bit_flags: int
    tag_count_meta: int
    has_large: bool
    has_visible_children: bool
    tag_string_general: str
    tag_string_character: str
    tag_string_copyright: str
    tag_string_artist: str
    tag_string_meta: str
    file_url: str
    large_file_url: str
    preview_file_url: str
