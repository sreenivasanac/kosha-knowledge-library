"""Items: list, detail, state changes (single and bulk, with undo), hard delete."""
import logging
import math
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, select, tuple_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, object_session

from kosha import storage
from kosha.api.tags import attach_tag, detach_tag, get_item
from kosha.auth import current_user
from kosha.config import DEFAULT_PAGE_SIZE, PAGE_SIZES
from kosha.db import get_session
from kosha.items_query import ItemFilters, conditions, fetch_page
from kosha.search.embeddings import EmbeddingError
from kosha.search.engine import MODES, search
from kosha.search.index import reindex
from kosha.search.query import QueryError
from kosha.models import DeletedItem, Item, ItemMedia, ItemRelation, ItemTag, ListItem, Tag, UndoBatch, User
from kosha.schemas import BulkIn, BulkOut, ItemDetailOut, ItemOut, ItemPage, ItemPatch
from kosha.serialize import serialize_items, thread_items

router = APIRouter(tags=["items"])
log = logging.getLogger(__name__)

# ItemPatch field -> the Item column it changes
PATCH_COLUMNS = {"starred": "starred", "note": "note", "read": "read_at", "archived": "archived_at"}
# Item columns an undo batch may hold (the MCP `edit_item` tool also replaces the summary)
UNDO_ITEM_COLUMNS = ("starred", "note", "read_at", "archived_at", "summary", "summary_method", "summary_status")
UNDO_DATETIME_COLUMNS = ("read_at", "archived_at")
ITEM_TAG_COLUMNS = ("item_id", "tag_id", "namespace", "role", "attached_by", "confidence", "removed_by_user")


def apply_patch(item: Item, patch: ItemPatch, now: datetime) -> None:
    """Star, note, read and archive are stored as given; read and archive keep
    the time they were first set."""
    fields = patch.model_fields_set
    if "starred" in fields and patch.starred is not None:
        item.starred = patch.starred
    if "note" in fields:
        item.note = patch.note or None
        reindex(object_session(item), [item.id])  # the note is searchable
    if "read" in fields and patch.read is not None:
        item.read_at = (item.read_at or now) if patch.read else None
    if "archived" in fields and patch.archived is not None:
        item.archived_at = (item.archived_at or now) if patch.archived else None


@router.get("/items", response_model=ItemPage)
def list_items(
    filters: Annotated[ItemFilters, Query()],
    q: str | None = Query(None, description="Search in the qualifier language (SEARCH_DESIGN.md B)"),
    mode: str | None = Query(None, description=f"One of {MODES}. Default: hybrid when q has words, else filter"),
    sort: str | None = Query(None, description="A sort key, optionally with -asc or -desc; same as sort: in q"),
    snapshot_id: int | None = Query(None, description="From the first page of a ranked search, to page through it"),
    rerank: Literal["none", "voyage", "jev"] = Query("none", description="Paid second stage on the top 50 of a ranked search"),
    weak: bool = Query(False, description="Also show the weaker matches a ranked search leaves out (see weak_hidden)"),
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, description=f"One of {PAGE_SIZES}"),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Numbered pages in a stable order (newest saved first). Saved, unarchived
    items by default; `archived=true` lists the Archive. With `q` the page is a
    search result: rows carry `snippet` and `score`, and a ranked search
    returns a `snapshot_id` that keeps its pages stable. A query that does not
    parse is a 422 with `{detail: {message, position}}`."""
    if page_size not in PAGE_SIZES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"page_size must be one of {PAGE_SIZES}")
    if mode is not None and mode not in MODES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"mode must be one of {MODES}")
    if q is None and sort is None:
        items, total = fetch_page(session, user.id, conditions(filters), page, page_size)
        return {
            "items": serialize_items(session, user.id, items), "page": page, "page_size": page_size,
            "total": total, "pages": math.ceil(total / page_size),
        }
    # With a query, saved/archived scope comes from the query (`is:archived`,
    # `include:`); the other list filters still apply when they were sent.
    sent = filters.model_copy(update={k: None for k in ("is_saved", "archived") if k not in filters.model_fields_set})
    try:
        found = search(session, user.id, q or "", mode=mode, sort=sort, page=page, page_size=page_size,
                       snapshot_id=snapshot_id, rerank=rerank, weak=weak, extra_where=conditions(sent),
                       extra_key=sent.model_dump_json(exclude_none=True))
    except QueryError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, exc.as_dict()) from None
    except EmbeddingError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"Semantic search is unavailable: {exc}") from None
    session.commit()  # the snapshot, the recent search and any ledger row
    rows = serialize_items(session, user.id, found["items"])
    for row in rows:
        row["snippet"], row["score"] = found["snippets"].get(row["id"]), found["scores"].get(row["id"])
    return {
        "items": rows, "page": page, "page_size": page_size, "total": found["total"],
        "pages": math.ceil(found["total"] / page_size),
        **{k: found[k] for k in ("mode", "query", "snapshot_id", "refreshed", "notice", "took_ms", "weak_hidden")},
    }


@router.get("/items/{item_id}", response_model=ItemDetailOut)
def get_one(item_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    item = get_item(session, user, item_id)
    out = serialize_items(session, user.id, [item], detail=True)[0]
    out["thread_items"] = serialize_items(session, user.id, thread_items(session, user.id, item))
    return out


@router.patch("/items/{item_id}", response_model=ItemOut)
def patch_one(item_id: int, body: ItemPatch, user: User = Depends(current_user), session: Session = Depends(get_session)):
    item = get_item(session, user, item_id)
    apply_patch(item, body, datetime.now(UTC))
    session.commit()
    return serialize_items(session, user.id, [item])[0]


@router.post("/items/bulk", response_model=BulkOut)
def bulk(body: BulkIn, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Apply the same change to many items. The state before the change is
    saved as an undo batch."""
    items = session.scalars(select(Item).where(Item.user_id == user.id, Item.id.in_(body.ids))).all()
    ids = [i.id for i in items]
    tag_ids = sorted(set(body.add_tag_ids) | set(body.remove_tag_ids))
    tags = {t.id: t for t in session.scalars(select(Tag).where(Tag.user_id == user.id, Tag.id.in_(tag_ids)))}
    if len(tags) != len(tag_ids):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tag not found")

    # Only what this action changes is kept for undo, so that undoing it does
    # not also revert a later edit to a field or a tag it never touched.
    columns = [column for name, column in PATCH_COLUMNS.items()
               if name in body.model_fields_set and (name == "note" or getattr(body, name) is not None)]
    plain = lambda value: value.isoformat() if isinstance(value, datetime) else value  # noqa: E731
    # The item_tags rows as they are now; bulk tagging only ever uses the role
    # "other", so no other tag's row changes.
    rows_before = {(row.item_id, row.tag_id): {c: getattr(row, c) for c in ITEM_TAG_COLUMNS} for row in session.scalars(
        select(ItemTag).where(ItemTag.item_id.in_(ids), ItemTag.tag_id.in_(tag_ids)))}
    before = {"items": [{"id": i.id, **{c: plain(getattr(i, c)) for c in columns}} for i in items], "tag_ids": tag_ids}
    changed: set[tuple[int, int]] = set()  # (item, tag) pairs this action attached or detached
    now = datetime.now(UTC)
    for item in items:
        apply_patch(item, body, now)
        for tag_id in body.add_tag_ids:
            existing = session.get(ItemTag, (item.id, tag_id))
            if existing is None or existing.removed_by_user:
                attach_tag(session, item.id, tags[tag_id])
                changed.add((item.id, tag_id))
        for tag_id in body.remove_tag_ids:
            existing = session.get(ItemTag, (item.id, tag_id))
            if existing is not None and not existing.removed_by_user:
                detach_tag(session, item.id, tag_id)
                changed.add((item.id, tag_id))
    before["tag_pairs"] = sorted(changed)
    before["item_tags"] = [rows_before[pair] for pair in before["tag_pairs"] if pair in rows_before]
    batch = UndoBatch(user_id=user.id, before=before)
    session.add(batch)
    session.commit()
    return {"updated": len(items), "undo_batch_id": batch.id}


def undo_batch(session: Session, user_id: int, batch_id: int) -> None:
    """Put back the state a bulk action or a pipeline write replaced. A batch
    can be undone once. Commits."""
    batch = session.get(UndoBatch, batch_id)
    if batch is None or batch.user_id != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Undo batch not found")
    if batch.undone_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Already undone")
    before = batch.before
    ids = [row["id"] for row in before["items"]]
    for row in before["items"]:
        # Only the columns the batch recorded go back (none for a pipeline tag write: only tag rows changed).
        values = {c: datetime.fromisoformat(row[c]) if c in UNDO_DATETIME_COLUMNS and row[c] else row[c]
                  for c in UNDO_ITEM_COLUMNS if c in row}
        if values:
            session.execute(update(Item).where(Item.id == row["id"], Item.user_id == user_id).values(**values))
    reindex(session, ids)  # notes are searchable
    # An agent's change to the tag tree (MCP `tags`): tags it created go, and
    # tags it renamed, moved, merged or deleted are put back as they were,
    # parents first; their item rows follow below.
    try:
        if before.get("created_tags"):
            session.execute(delete(Tag).where(Tag.user_id == user_id, Tag.id.in_(before["created_tags"])))
        for row in sorted(before.get("tags", []), key=lambda r: r["path"].count(".")):
            tag = session.get(Tag, row["id"])
            if tag is None:
                session.add(Tag(user_id=user_id, **row))
            elif tag.user_id == user_id:
                for column, value in row.items():
                    setattr(tag, column, value)
            session.flush()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot undo: a tag with the same name now exists in that place") from None
    if before["tag_ids"]:
        # A bulk action lists the (item, tag) pairs it changed; other writers replace every row of their items and tags.
        pairs = before.get("tag_pairs")
        if pairs is None:
            session.execute(delete(ItemTag).where(ItemTag.item_id.in_(ids), ItemTag.tag_id.in_(before["tag_ids"])))
        elif pairs:
            session.execute(delete(ItemTag).where(tuple_(ItemTag.item_id, ItemTag.tag_id).in_([tuple(p) for p in pairs])))
        # Tags and items may have been deleted since; their rows are not restored.
        tag_ids = [row["tag_id"] for row in before["item_tags"]]
        live_tags = set(session.scalars(select(Tag.id).where(Tag.user_id == user_id, Tag.id.in_(tag_ids))))
        live_items = set(session.scalars(select(Item.id).where(Item.user_id == user_id, Item.id.in_(ids))))
        for row in before["item_tags"]:
            if row["tag_id"] not in live_tags or row["item_id"] not in live_items:
                continue
            if row["role"] != "other" and not row["removed_by_user"]:
                # Another tag may have taken the role since: it steps down.
                session.execute(update(ItemTag).where(
                    ItemTag.item_id == row["item_id"], ItemTag.namespace == row["namespace"],
                    ItemTag.role == row["role"]).values(role="other"))
            session.add(ItemTag(**row))
    if before.get("list"):  # the pipeline `list` stage: take out what it appended
        session.execute(delete(ListItem).where(ListItem.list_id == before["list"]["id"],
                                               ListItem.item_id.in_(before["list"]["added"])))
    batch.undone_at = datetime.now(UTC)
    session.commit()


@router.post("/undo/{batch_id}", status_code=status.HTTP_204_NO_CONTENT)
def undo(batch_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Put back the state a bulk action replaced. A batch can be undone once."""
    undo_batch(session, user.id, batch_id)


def remove_media_files(paths: list[str]) -> None:
    """Delete stored media files (their keys in the media storage). Call after
    the commit that removed their rows, so a failed delete never orphans rows."""
    if not paths:
        return
    store = storage.get_storage()
    for key in paths:
        try:
            store.delete(key)
        except storage.ERRORS:  # unreachable storage or a bad key: the row is gone either way
            log.warning("Could not delete stored media file %s", key)


def delete_item(session: Session, user: User, item: Item) -> None:
    """Delete for good: the row, its stored files, and a `deleted_items` record
    so a later sync does not bring the item back. Commits."""
    files = [p for m in session.scalars(select(ItemMedia).where(ItemMedia.item_id == item.id))
             for p in (m.local_path, m.local_preview_path) if p]
    if session.scalar(select(DeletedItem.id).where(
            DeletedItem.user_id == user.id, DeletedItem.source == item.source,
            DeletedItem.source_id == item.source_id)) is None:
        session.add(DeletedItem(user_id=user.id, source=item.source, source_id=item.source_id))
    # Posts that quoted or replied to this one point at a missing target again.
    session.execute(update(ItemRelation).where(ItemRelation.to_item_id == item.id).values(to_item_id=None, target_state="missing"))
    session.delete(item)
    session.commit()
    remove_media_files(files)


@router.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def hard_delete(item_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    delete_item(session, user, get_item(session, user, item_id))
