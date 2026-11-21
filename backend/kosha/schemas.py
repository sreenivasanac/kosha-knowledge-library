"""Pydantic request and response shapes for the REST API."""
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Role = Literal["primary", "secondary", "other"]


# --- Captures ---------------------------------------------------------------
class CaptureIn(BaseModel):
    source: Literal["x", "linkedin"]
    kind: str
    meta: dict[str, Any] | None = None
    payload: dict[str, Any] | list[Any]


class CaptureOut(BaseModel):
    capture_id: int
    items_new: int
    items_updated: int
    items_unchanged: int


# --- Items ------------------------------------------------------------------
class AuthorOut(BaseModel):
    name: str | None
    handle: str | None
    avatar: str | None


class MediaOut(BaseModel):
    id: int
    type: str
    position: int
    url: str  # /media/{id}: the stored file, or a redirect to the remote one
    preview_url: str | None
    remote_url: str | None
    stored: bool
    mime_type: str | None
    width: int | None
    height: int | None
    duration_ms: int | None
    alt_text: str | None
    variants: list[dict[str, Any]] | None
    extract_status: str
    extract_kind: str | None
    extract_text: str | None = None  # item detail only


class LinkOut(BaseModel):
    url: str
    domain: str | None
    title: str | None
    description: str | None
    image_url: str | None


class ItemTagOut(BaseModel):
    tag_id: int
    name: str
    path: list[str]  # names from the root, for the breadcrumb
    path_ids: str  # ltree of tag ids
    role: Role
    status: str
    color: str | None
    attached_by: str
    confidence: float | None


class CollectionRef(BaseModel):
    id: int
    name: str
    kind: str


class RelationOut(BaseModel):
    """A pointer to another post: the quoted one, or the one replied to."""

    source_id: str
    item_id: int | None
    target_state: str  # held, missing, unavailable
    author_handle: str | None = None


class ThreadInfo(BaseModel):
    root_source_id: str
    member_count: int  # posts of the thread held, besides the root


class ItemOut(BaseModel):
    id: int
    seq: int
    source: str
    source_id: str
    is_saved: bool
    url: str | None
    title: str | None
    body: str | None
    summary: str | None
    author: AuthorOut
    posted_at: datetime | None
    saved_at: datetime | None  # the real time, or the estimate
    saved_at_approximate: bool
    saved_order: int | None
    captured_at: datetime
    content_types: list[str]
    lang: str | None
    likes: int | None
    reposts: int | None
    replies: int | None
    views: int | None
    duration_s: int | None
    starred: bool
    note: str | None
    read: bool
    read_at: datetime | None
    archived: bool
    archived_at: datetime | None
    unavailable_reason: str | None
    media: list[MediaOut]
    links: list[LinkOut]
    tags: list[ItemTagOut]
    collections: list[CollectionRef]
    quote: RelationOut | None  # set when this post quotes another
    quoted: "ItemOut | None" = None  # the quoted post, nested one level, when held
    reply_to: RelationOut | None
    thread: ThreadInfo | None


class ItemDetailOut(ItemOut):
    thread_items: list[ItemOut] = []  # the whole thread held, oldest first


class ItemPage(BaseModel):
    items: list[ItemOut]
    page: int
    page_size: int
    total: int
    pages: int


class ItemPatch(BaseModel):
    starred: bool | None = None
    note: str | None = None
    read: bool | None = None
    archived: bool | None = None


class BulkIn(ItemPatch):
    ids: list[int] = Field(min_length=1)
    add_tag_ids: list[int] = []
    remove_tag_ids: list[int] = []


class BulkOut(BaseModel):
    updated: int
    undo_batch_id: int


# --- Tags -------------------------------------------------------------------
class TagOut(BaseModel):
    id: int
    parent_id: int | None
    path_ids: str
    name: str
    slug: str
    description: str | None
    status: str
    origin: str
    namespace: str
    color: str | None
    sort: int


class TagNode(TagOut):
    own_count: int  # items carrying exactly this tag
    count: int  # items carrying this tag or any tag beneath it
    children: list["TagNode"] = []


class TagCreate(BaseModel):
    name: str = Field(min_length=1)
    parent_id: int | None = None
    description: str | None = None
    color: str | None = None
    namespace: str | None = None  # defaults to the parent's, else "main"
    status: Literal["proposed", "approved"] = "approved"
    origin: Literal["human", "agent", "import"] = "human"


class TagPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    description: str | None = None
    color: str | None = None
    sort: int | None = None


class TagMove(BaseModel):
    parent_id: int | None


class TagMerge(BaseModel):
    into_id: int


class TagAttach(BaseModel):
    role: Role = "other"


# --- Sidebar, stats, settings -------------------------------------------------
class CollectionOut(CollectionRef):
    source: str
    source_id: str
    count: int


class FacetCount(BaseModel):
    value: str
    count: int


class Facets(BaseModel):
    sources: list[FacetCount]
    years: list[FacetCount]
