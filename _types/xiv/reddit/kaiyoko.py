from __future__ import annotations

from typing import Any, Literal, TypedDict

__all__ = ("TopLevelListingResponse",)


class ChildListingDataResponse(TypedDict):
    approved_at_utc: str | None  # maybe? UTC timestamp
    subreddit: str
    selftext: str
    author_fullname: str
    saved: bool
    title: str
    link_flair_richtext: list[dict[str, str]]  # expand?
    subreddit_name_prefixed: str
    hidden: bool
    pwls: int
    link_flair_css_class: str
    downs: int
    thumbnail_height: int
    top_awarded_type: Any | None  # TODO?
    hide_score: bool
    name: str
    quarantine: bool
    link_flair_text_colour: str
    upvote_ratio: float
    author_flair_background_colour: str
    ups: int
    total_awards_received: int
    media_embed: ...  # this is some object, idk yet
    thumbnail_width: int
    author_flair_template_id: str
    is_original_content: bool
    user_reports: list[Any]  # unsure
    secure_media: Any | None  # unsure
    is_reddit_media_domain: bool
    is_meta: bool
    category: Any | None  # unsure
    secure_media_embed: ...  # this is some object, idk yet
    link_flair_text: str
    can_mod_post: bool
    score: int
    approved_by: Any | None  # unsure
    is_created_from_ads_ui: bool
    author_premium: bool
    thumbnail: str
    edited: bool
    author_flair_css_class: Any | None  # unsure
    author_flair_richtext: list[dict[str, str]]
    gildings: dict[str, int]
    post_hint: str
    content_categories: Any | None  # unsure
    is_self: bool
    subreddit_type: str
    created: int
    link_flair_type: str
    wls: int
    removed_by_category: Any | None  # unsure
    banned_by: Any | None  # unsure
    author_flair_type: str
    domain: str
    allow_live_commands: bool
    selftext_html: Any | None  # unsure
    likes: Any | None  # unsure
    suggested_sort: Any | None  # unsure
    banned_at_utc: str | None
    url_overriden_by_dest: str
    view_count: Any | None  # unsure
    archived: bool
    no_follow: bool
    is_crosspostable: bool
    pinned: bool
    over_18: bool
    preview: ...  # TODO?
    all_awardings: list[Any]  # some objects: #TODO?
    awarders: list[Any]
    media_only: bool
    link_flair_template_id: str
    can_gild: bool
    spoiler: bool
    locked: bool
    author_flair_text: str
    treatment_tags: list[Any]
    visited: bool
    removed_by: Any | None
    mod_note: Any | None
    distinguished: Any | None
    subreddit_id: str
    author_is_blocked: bool
    mod_reason_by: Any | None
    num_reports: Any | None
    remmoval_reason: Any | None
    link_flair_background_color: str
    id: str
    is_robot_indexable: bool
    report_reasons: Any | None
    author: str
    discussion_type: Any | None
    num_comments: int
    send_replies: bool
    whitelist_status: str
    contest_mode: bool
    mod_reports: list[Any]
    author_patreon_flair: bool
    author_flair_text_color: str
    permalink: str
    parent_whitelist_status: str
    stickied: bool
    url: str
    subreddit_subscribers: int
    created_utc: int
    num_crossposts: int
    media: Any | None
    is_video: bool


class ChildListingResponse(TypedDict):
    kind: str
    data: ChildListingDataResponse


class TopLevelListingDataResponse(TypedDict):
    after: str
    dist: int
    modhash: str
    geo_filter: str
    children: list[ChildListingResponse]
    before: str | None


class TopLevelListingResponse(TypedDict):
    kind: Literal["Listing"]
    data: TopLevelListingDataResponse
