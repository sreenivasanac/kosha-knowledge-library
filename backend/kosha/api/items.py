"""Items: list, detail, state changes (single and bulk, with undo), hard delete."""
import math
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session, object_session

from kosha.api.tags import attach_tag, detach_tag, get_item
from kosha.auth import current_user
from kosha.config import DEFAULT_PAGE_SIZE, PAGE_SIZES, get_settings
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
                       snapshot_id=snapshot_id, extra_where=conditions(sent),
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
        **{k: found[k] for k in ("mode", "query", "snapshot_id", "refreshed", "notice", "took_ms")},
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

    before = {
        "items": [{"id": i.id, "starred": i.starred, "note": i.note,
                   "read_at": i.read_at.isoformat() if i.read_at else None,
                   "archived_at": i.archived_at.isoformat() if i.archived_at else None} for i in items],
        "tag_ids": tag_ids,
        # The touched item_tags rows as they were; bulk tagging only ever uses
        # the role "other", so no other tag's row changes.
        "item_tags": [{c: getattr(row, c) for c in ITEM_TAG_COLUMNS} for row in session.scalars(
            select(ItemTag).where(ItemTag.item_id.in_(ids), ItemTag.tag_id.in_(tag_ids)))],
    }
    now = datetime.now(UTC)
    for item in items:
        apply_patch(item, body, now)
        for tag_id in body.add_tag_ids:
            existing = session.get(ItemTag, (item.id, tag_id))
            if existing is None or existing.removed_by_user:
                attach_tag(session, item.id, tags[tag_id])
        for tag_id in body.remove_tag_ids:
            detach_tag(session, item.id, tag_id)
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
        if "starred" not in row:  # a pipeline tag write: only tag rows changed
            continue
        session.execute(update(Item).where(Item.id == row["id"], Item.user_id == user_id).values(
            starred=row["starred"], note=row["note"],
            read_at=datetime.fromisoformat(row["read_at"]) if row["read_at"] else None,
            archived_at=datetime.fromisoformat(row["archived_at"]) if row["archived_at"] else None,
        ))
    reindex(session, ids)  # notes are searchable
    if before["tag_ids"]:
        session.execute(delete(ItemTag).where(ItemTag.item_id.in_(ids), ItemTag.tag_id.in_(before["tag_ids"])))
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
    root = get_settings().kosha_media_dir.resolve()
    for path in files:  # after the commit, so a failed delete never orphans rows
        target = (root / path).resolve()  # paths are stored relative to the media folder
        if target.is_relative_to(root):
            target.unlink(missing_ok=True)


@router.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def hard_delete(item_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    delete_item(session, user, get_item(session, user, item_id))
