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
    headline: str | None = None  # LinkedIn: the line under the author's name


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
    # Search results only. `snippet` is HTML-escaped text whose only tags are
    # <mark>…</mark> around the matching words; `score` is the fused rank score.
    snippet: str | None = None
    score: float | None = None


class ItemDetailOut(ItemOut):
    thread_items: list[ItemOut] = []  # the whole thread held, oldest first


class ItemPage(BaseModel):
    items: list[ItemOut]
    page: int
    page_size: int
    total: int
    pages: int
    # Set when `q` was given.
    mode: str | None = None  # hybrid, keyword, semantic or filter: what actually ran
    query: str | None = None  # the canonical form of `q`
    # A ranked result is a stored snapshot; send this back to page through it.
    snapshot_id: int | None = None
    refreshed: bool = False  # the snapshot sent had expired and the search ran again
    notice: str | None = None  # no_exact_matches | fuzzy_match | semantic_unavailable | rerank_unavailable
    weak_hidden: int = 0  # weaker matches left out of a ranked result; `weak=true` shows them
    took_ms: float | None = None


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


# --- Search bar, saved views, lists, removals ---------------------------------
class Suggestion(BaseModel):
    insert: str  # text that replaces `replace_range`
    label: str
    count: int | None  # results if this is added to the rest of the bar
    kind: str  # key, value, author, tag, set, recent, view, hint


class SuggestOut(BaseModel):
    replace_range: tuple[int, int]
    suggestions: list[Suggestion]


class ParseIn(BaseModel):
    q: str


class QueryErrorOut(BaseModel):
    message: str
    position: int


class ParseOut(BaseModel):
    ok: bool
    canonical: str | None = None
    # One per token: {start, end, text, kind (text|qualifier|tag|set|operator), negated, key?, value?}
    chips: list[dict[str, Any]] = []
    has_text: bool = False  # there are words to rank by, so the search will be hybrid
    error: QueryErrorOut | None = None


class SavedViewIn(BaseModel):
    name: str = Field(min_length=1)
    query: str = ""
    params: dict[str, Any] | None = None  # the UI's own state (mode, sort, layout)


class SavedViewPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    query: str | None = None
    params: dict[str, Any] | None = None


class SavedViewOut(SavedViewIn):
    id: int
    created_at: datetime


class ListIn(BaseModel):
    name: str = Field(min_length=1)
    description: str | None = None


class ListPatch(BaseModel):
    name: str | None = Field(None, min_length=1)
    description: str | None = None


class ListOut(ListIn):
    id: int
    created_at: datetime
    count: int


class ListDetailOut(ListOut):
    items: list[ItemOut]  # in the list's own order


class ListItemsIn(BaseModel):
    item_ids: list[int] = Field(min_length=1)


class RemovalOut(BaseModel):
    item_id: int
    source: str
    source_id: str  # X: the numeric post id; LinkedIn: urn:li:activity:…
    url: str | None


class RemovalResultIn(BaseModel):
    item_id: int
    status: Literal["removed", "already_gone", "failed"]
    detail: str | None = None
