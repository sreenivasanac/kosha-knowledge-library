"""SQLAlchemy models for every table in DESIGN.md section 4.

Owned tables carry `user_id`. Child tables (media, links, relations, item_tags,
...) inherit ownership from their item and do not repeat it.
"""
from datetime import datetime
from decimal import Decimal

from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Sequence,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import UserDefinedType

from kosha.config import DEFAULT_NAMESPACE, EMBEDDING_DIM


class Ltree(UserDefinedType):
    """Postgres `ltree`; values travel as dotted strings ("12.88.340")."""

    cache_ok = True

    def get_col_spec(self, **kw):
        return "LTREE"


class Base(DeclarativeBase):
    pass


TZ = DateTime(timezone=True)
NOW = func.now()


def _pk() -> Mapped[int]:
    return mapped_column(BigInteger, primary_key=True, autoincrement=True)


def _user() -> Mapped[int]:
    return mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)


def _item(pk: bool = False) -> Mapped[int]:
    return mapped_column(ForeignKey("items.id", ondelete="CASCADE"), primary_key=pk, index=not pk)


# --- Accounts ---------------------------------------------------------------
class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = _pk()
    email: Mapped[str] = mapped_column(Text, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=NOW)


class ApiToken(Base):
    """Bearer tokens for the extension and MCP. Only the SHA-256 is stored."""

    __tablename__ = "api_tokens"
    id: Mapped[int] = _pk()
    user_id: Mapped[int] = _user()
    name: Mapped[str] = mapped_column(Text)
    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=NOW)
    last_used_at: Mapped[datetime | None] = mapped_column(TZ)


class Setting(Base):
    __tablename__ = "settings"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSONB)


class Keybinding(Base):
    __tablename__ = "keybindings"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    action: Mapped[str] = mapped_column(Text, primary_key=True)
    keys: Mapped[str] = mapped_column(Text)


# --- Items ------------------------------------------------------------------
items_seq = Sequence("items_seq")

# Weighted full-text vector, kept by Postgres itself. Weights follow
# SEARCH_DESIGN.md B.6 as far as one row can supply them (quoted text, link
# titles and extracts live in other tables; the search work adds those).
ITEMS_TSV_SQL = """
setweight(to_tsvector('simple', coalesce(title, '')), 'A') ||
setweight(to_tsvector('simple', coalesce(author_name, '') || ' ' || coalesce(author_handle, '')), 'A') ||
setweight(to_tsvector(CASE WHEN lang = 'en' THEN 'english'::regconfig ELSE 'simple'::regconfig END,
                      coalesce(body, '')), 'B') ||
setweight(to_tsvector('simple', coalesce(summary, '')), 'D')
"""


class Item(Base):
    __tablename__ = "items"
    id: Mapped[int] = _pk()
    user_id: Mapped[int] = _user()
    # Stable number for pagination; never changes after insert.
    seq: Mapped[int] = mapped_column(BigInteger, items_seq, server_default=items_seq.next_value(), unique=True)
    source: Mapped[str] = mapped_column(Text)
    source_id: Mapped[str] = mapped_column(Text)
    is_saved: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    # X: the raw bookmark-timeline sortIndex. Larger means saved later.
    saved_order: Mapped[int | None] = mapped_column(BigInteger)
    saved_at: Mapped[datetime | None] = mapped_column(TZ)
    saved_at_est: Mapped[datetime | None] = mapped_column(TZ)
    captured_at: Mapped[datetime] = mapped_column(TZ, server_default=NOW)
    content_types: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    url: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text)
    author_name: Mapped[str | None] = mapped_column(Text)
    author_handle: Mapped[str | None] = mapped_column(Text)
    author_avatar: Mapped[str | None] = mapped_column(Text)
    posted_at: Mapped[datetime | None] = mapped_column(TZ)
    lang: Mapped[str | None] = mapped_column(Text)
    likes: Mapped[int | None] = mapped_column(BigInteger)
    reposts: Mapped[int | None] = mapped_column(BigInteger)
    replies: Mapped[int | None] = mapped_column(BigInteger)
    views: Mapped[int | None] = mapped_column(BigInteger)
    duration_s: Mapped[int | None] = mapped_column(Integer)
    summary: Mapped[str | None] = mapped_column(Text)
    summary_method: Mapped[str | None] = mapped_column(Text)
    summary_status: Mapped[str] = mapped_column(Text, server_default="none")
    starred: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    note: Mapped[str | None] = mapped_column(Text)
    read_at: Mapped[datetime | None] = mapped_column(TZ)
    archived_at: Mapped[datetime | None] = mapped_column(TZ)
    content_hash: Mapped[str | None] = mapped_column(Text)
    # Set when the source says the post is deleted, private or withheld; the
    # row is kept (with `raw`) so a bookmark is never dropped silently.
    unavailable_reason: Mapped[str | None] = mapped_column(Text)
    # Original payloads, one key per capture format ("graphql", "api", ...).
    # Deferred: large, and list queries never need it.
    raw: Mapped[dict | None] = mapped_column(JSONB, deferred=True)
    tsv: Mapped[str | None] = mapped_column(TSVECTOR, Computed(ITEMS_TSV_SQL, persisted=True))

    __table_args__ = (
        UniqueConstraint("user_id", "source", "source_id"),
        Index("ix_items_tsv", "tsv", postgresql_using="gin"),
        Index("ix_items_body_trgm", "body", postgresql_using="gin", postgresql_ops={"body": "gin_trgm_ops"}),
        Index("ix_items_title_trgm", "title", postgresql_using="gin", postgresql_ops={"title": "gin_trgm_ops"}),
        Index("ix_items_order", "user_id", text("saved_order DESC NULLS FIRST"), text("seq DESC")),
    )


class ItemMedia(Base):
    __tablename__ = "item_media"
    id: Mapped[int] = _pk()
    item_id: Mapped[int] = _item()
    # The source's own key for the file (X media_key, else the URL); lets two
    # captures of the same post merge into one row.
    source_key: Mapped[str] = mapped_column(Text)
    type: Mapped[str] = mapped_column(Text)  # photo, video, animated_gif, audio, document
    mime_type: Mapped[str | None] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    original_url: Mapped[str | None] = mapped_column(Text)
    preview_url: Mapped[str | None] = mapped_column(Text)
    variants: Mapped[list | None] = mapped_column(JSONB)
    local_path: Mapped[str | None] = mapped_column(Text)
    local_preview_path: Mapped[str | None] = mapped_column(Text)
    bytes: Mapped[int | None] = mapped_column(BigInteger)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    alt_text: Mapped[str | None] = mapped_column(Text)
    extract_text: Mapped[str | None] = mapped_column(Text)
    extract_kind: Mapped[str | None] = mapped_column(Text)  # transcript, description, ocr
    extract_format: Mapped[str | None] = mapped_column(Text)  # plain, timestamped
    extract_status: Mapped[str] = mapped_column(Text, server_default="none")
    extract_model: Mapped[str | None] = mapped_column(Text)
    extract_lang: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (UniqueConstraint("item_id", "source_key"),)


class ItemRelation(Base):
    __tablename__ = "item_relations"
    id: Mapped[int] = _pk()
    from_item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    relation: Mapped[str] = mapped_column(Text)  # quotes, replies_to, thread_member
    to_item_id: Mapped[int | None] = mapped_column(ForeignKey("items.id", ondelete="SET NULL"), index=True)
    to_source_id: Mapped[str] = mapped_column(Text, index=True)
    position: Mapped[int | None] = mapped_column(Integer)
    target_state: Mapped[str] = mapped_column(Text, server_default="missing")  # held, missing, unavailable

    __table_args__ = (UniqueConstraint("from_item_id", "relation", "to_source_id"),)


class ItemLink(Base):
    __tablename__ = "item_links"
    id: Mapped[int] = _pk()
    item_id: Mapped[int] = _item()
    url: Mapped[str] = mapped_column(Text)  # expanded
    domain: Mapped[str | None] = mapped_column(Text, index=True)
    title: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, server_default=text("0"))

    __table_args__ = (UniqueConstraint("item_id", "url"),)


# --- Tags -------------------------------------------------------------------
tags_id_seq = Sequence("tags_id_seq")


class Tag(Base):
    __tablename__ = "tags"
    # The id comes from an explicit sequence so `path` can contain it at insert.
    id: Mapped[int] = mapped_column(BigInteger, tags_id_seq, server_default=tags_id_seq.next_value(), primary_key=True)
    user_id: Mapped[int] = _user()
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), index=True)
    # Dotted tag ids from the root to this tag. Rename changes no path; move
    # rewrites the subtree's paths.
    path: Mapped[str] = mapped_column(Ltree)
    name: Mapped[str] = mapped_column(Text)
    slug: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="approved")  # proposed, approved, rejected
    origin: Mapped[str] = mapped_column(Text, server_default="human")  # human, agent, import
    namespace: Mapped[str] = mapped_column(Text, server_default=DEFAULT_NAMESPACE)
    color: Mapped[str | None] = mapped_column(Text)
    sort: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=NOW)

    __table_args__ = (
        # Sibling names are unique; top-level tags (NULL parent) count as siblings.
        UniqueConstraint("user_id", "namespace", "parent_id", "slug", postgresql_nulls_not_distinct=True),
        Index("ix_tags_path", "path", postgresql_using="gist"),
    )


class ItemTag(Base):
    __tablename__ = "item_tags"
    item_id: Mapped[int] = _item(pk=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True)
    namespace: Mapped[str] = mapped_column(Text, server_default=DEFAULT_NAMESPACE)  # copied from the tag
    role: Mapped[str] = mapped_column(Text, server_default="other")  # primary, secondary, other
    attached_by: Mapped[str] = mapped_column(Text, server_default="human")  # human, agent, import
    confidence: Mapped[float | None] = mapped_column(Float)
    # A tag the user removed stays recorded so an agent does not put it back.
    removed_by_user: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=NOW)

    __table_args__ = (
        Index("ix_item_tags_tag_item", "tag_id", "item_id"),
        Index("uq_item_tags_primary", "item_id", "namespace", unique=True,
              postgresql_where=text("role = 'primary' AND NOT removed_by_user")),
        Index("uq_item_tags_secondary", "item_id", "namespace", unique=True,
              postgresql_where=text("role = 'secondary' AND NOT removed_by_user")),
    )


# --- Source folders ---------------------------------------------------------
class Collection(Base):
    __tablename__ = "collections"
    id: Mapped[int] = _pk()
    user_id: Mapped[int] = _user()
    source: Mapped[str] = mapped_column(Text)
    source_id: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)  # x_folder, youtube_playlist, linkedin, dewey_folder, browser_folder
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=NOW)

    __table_args__ = (UniqueConstraint("user_id", "source", "source_id"),)


class ItemCollection(Base):
    __tablename__ = "item_collections"
    item_id: Mapped[int] = _item(pk=True)
    collection_id: Mapped[int] = mapped_column(ForeignKey("collections.id", ondelete="CASCADE"), primary_key=True, index=True)
    position: Mapped[int | None] = mapped_column(BigInteger)  # the folder timeline's own sortIndex


# --- Search / pipeline support (filled by later work) ------------------------
class ItemEmbedding(Base):
    __tablename__ = "item_embeddings"
    item_id: Mapped[int] = _item(pk=True)
    model: Mapped[str] = mapped_column(Text, primary_key=True)
    content_hash: Mapped[str] = mapped_column(Text)
    vector: Mapped[list[float]] = mapped_column(HALFVEC(EMBEDDING_DIM))


class ResultSet(Base):
    __tablename__ = "result_sets"
    id: Mapped[int] = _pk()
    user_id: Mapped[int] = _user()
    name: Mapped[str] = mapped_column(Text)
    query: Mapped[str | None] = mapped_column(Text)
    item_ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), server_default=text("'{}'"))
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=NOW)
    expires_at: Mapped[datetime | None] = mapped_column(TZ)

    __table_args__ = (UniqueConstraint("user_id", "name"),)


class JevQuestion(Base):
    __tablename__ = "jev_questions"
    id: Mapped[int] = _pk()
    user_id: Mapped[int] = _user()
    question: Mapped[str] = mapped_column(Text)
    question_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=NOW)

    __table_args__ = (UniqueConstraint("user_id", "question_hash"),)


class JevAnswer(Base):
    __tablename__ = "jev_answers"
    id: Mapped[int] = _pk()
    question_id: Mapped[int] = mapped_column(ForeignKey("jev_questions.id", ondelete="CASCADE"))
    item_id: Mapped[int] = _item()
    summary_hash: Mapped[str] = mapped_column(Text)
    score: Mapped[float | None] = mapped_column(Float)
    answer: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=NOW)

    __table_args__ = (UniqueConstraint("question_id", "item_id", "summary_hash"),)


class LabelRun(Base):
    __tablename__ = "label_runs"
    id: Mapped[int] = _pk()
    user_id: Mapped[int] = _user()
    strategy: Mapped[str] = mapped_column(Text)
    namespace: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="running")
    coverage: Mapped[float | None] = mapped_column(Float)
    spend_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), server_default=text("0"))
    params: Mapped[dict | None] = mapped_column(JSONB)
    started_at: Mapped[datetime] = mapped_column(TZ, server_default=NOW)
    finished_at: Mapped[datetime | None] = mapped_column(TZ)


class SpendLedger(Base):
    """Every paid call (X, Voyage, Jev, LLM, speech-to-text)."""

    __tablename__ = "spend_ledger"
    id: Mapped[int] = _pk()
    user_id: Mapped[int] = _user()
    provider: Mapped[str] = mapped_column(Text)
    operation: Mapped[str] = mapped_column(Text)
    units: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    meta: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=NOW)


class List(Base):
    __tablename__ = "lists"
    id: Mapped[int] = _pk()
    user_id: Mapped[int] = _user()
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=NOW)


class ListItem(Base):
    __tablename__ = "list_items"
    list_id: Mapped[int] = mapped_column(ForeignKey("lists.id", ondelete="CASCADE"), primary_key=True)
    item_id: Mapped[int] = _item(pk=True)
    position: Mapped[int | None] = mapped_column(Integer)
    added_at: Mapped[datetime] = mapped_column(TZ, server_default=NOW)


class SavedView(Base):
    __tablename__ = "saved_views"
    id: Mapped[int] = _pk()
    user_id: Mapped[int] = _user()
    name: Mapped[str] = mapped_column(Text)
    query: Mapped[str] = mapped_column(Text, server_default="")
    params: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=NOW)


# --- Sync and capture -------------------------------------------------------
class SyncState(Base):
    __tablename__ = "sync_state"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    source: Mapped[str] = mapped_column(Text, primary_key=True)
    state: Mapped[dict | None] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(TZ, server_default=NOW, onupdate=NOW)


class Capture(Base):
    """A raw envelope exactly as received (docs/CAPTURE_CONTRACT.md)."""

    __tablename__ = "captures"
    id: Mapped[int] = _pk()
    user_id: Mapped[int] = _user()
    source: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict | None] = mapped_column(JSONB)
    payload: Mapped[dict | list | None] = mapped_column(JSONB)
    # Hash of source + kind + payload: posting the same batch twice reuses the row.
    payload_hash: Mapped[str] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(TZ, server_default=NOW)
    parsed_at: Mapped[datetime | None] = mapped_column(TZ)
    parse_error: Mapped[str | None] = mapped_column(Text)
    items_new: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    items_updated: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    items_unchanged: Mapped[int] = mapped_column(Integer, server_default=text("0"))

    __table_args__ = (UniqueConstraint("user_id", "payload_hash"),)


class DeletedItem(Base):
    """Hard-deleted items, so a later sync does not bring them back."""

    __tablename__ = "deleted_items"
    id: Mapped[int] = _pk()
    user_id: Mapped[int] = _user()
    source: Mapped[str] = mapped_column(Text)
    source_id: Mapped[str] = mapped_column(Text)
    deleted_at: Mapped[datetime] = mapped_column(TZ, server_default=NOW)

    __table_args__ = (UniqueConstraint("user_id", "source", "source_id"),)


class UndoBatch(Base):
    """Prior state of the rows one bulk action touched (not in DESIGN.md's list;
    needed for `POST /api/undo/{batch_id}`)."""

    __tablename__ = "undo_batches"
    id: Mapped[int] = _pk()
    user_id: Mapped[int] = _user()
    before: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=NOW)
    undone_at: Mapped[datetime | None] = mapped_column(TZ)
