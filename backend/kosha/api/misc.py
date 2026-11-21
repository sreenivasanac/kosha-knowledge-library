"""Sidebar data (folders, source and year facets), stats, settings, keybindings."""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from kosha.auth import current_user
from kosha.config import DEFAULT_SETTINGS, PAGE_SIZES
from kosha.db import get_session
from kosha.items_query import ITEM_YEAR
from kosha.models import Collection, Item, ItemCollection, Keybinding, Setting, User
from kosha.prefs import load_settings
from kosha.schemas import CollectionOut, Facets

router = APIRouter(tags=["library"])

# Lists show saved items that are not in the Archive; counts match that.
LISTED = (Item.is_saved, Item.archived_at.is_(None))


@router.get("/health")
def health():
    """2xx when the bearer token or session is valid (the router requires
    one), 401 otherwise. The extension uses it to test its connection."""
    return {"status": "ok"}


@router.get("/collections", response_model=list[CollectionOut])
def collections(user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Source folders (X bookmark folders, ...) with item counts."""
    count = func.count(Item.id)
    rows = session.execute(
        select(Collection, count)
        .outerjoin(ItemCollection, ItemCollection.collection_id == Collection.id)
        .outerjoin(Item, (Item.id == ItemCollection.item_id) & Item.is_saved & Item.archived_at.is_(None))
        .where(Collection.user_id == user.id).group_by(Collection.id).order_by(Collection.name)
    )
    return [{"id": c.id, "name": c.name, "kind": c.kind, "source": c.source, "source_id": c.source_id, "count": n}
            for c, n in rows]


@router.get("/facets", response_model=Facets)
def facets(user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Counts per source and per year saved (estimated where the source gives
    no save time; the post's own date as the last resort)."""
    scope = (Item.user_id == user.id, *LISTED)
    sources = session.execute(select(Item.source, func.count()).where(*scope).group_by(Item.source).order_by(Item.source))
    years = session.execute(
        select(ITEM_YEAR.label("year"), func.count()).where(*scope, ITEM_YEAR.is_not(None))
        .group_by(text("year")).order_by(text("year DESC")))
    return {"sources": [{"value": s, "count": n} for s, n in sources],
            "years": [{"value": str(int(y)), "count": n} for y, n in years]}


@router.get("/stats", response_model=dict[str, Any])
def stats(user: User = Depends(current_user), session: Session = Depends(get_session)):
    row = session.execute(text("""
        SELECT
          count(*) FILTER (WHERE is_saved AND archived_at IS NULL) AS items,
          count(*) FILTER (WHERE NOT is_saved) AS context_items,
          count(*) FILTER (WHERE is_saved AND archived_at IS NOT NULL) AS archived,
          count(*) FILTER (WHERE is_saved AND archived_at IS NULL AND starred) AS starred,
          count(*) FILTER (WHERE is_saved AND archived_at IS NULL AND read_at IS NULL) AS unread,
          count(*) FILTER (WHERE is_saved AND archived_at IS NULL AND unavailable_reason IS NOT NULL) AS unavailable,
          count(*) FILTER (WHERE is_saved AND archived_at IS NULL AND NOT EXISTS (
              SELECT 1 FROM item_tags t WHERE t.item_id = items.id AND NOT t.removed_by_user)) AS untagged
        FROM items WHERE user_id = :u
    """), {"u": user.id}).one()._asdict()
    extra = session.execute(text("""
        SELECT
          (SELECT count(*) FROM item_media m JOIN items i ON i.id = m.item_id WHERE i.user_id = :u) AS media,
          (SELECT count(*) FROM item_media m JOIN items i ON i.id = m.item_id
            WHERE i.user_id = :u AND m.local_path IS NOT NULL) AS media_stored,
          (SELECT count(*) FROM tags WHERE user_id = :u) AS tags,
          (SELECT count(*) FROM collections WHERE user_id = :u) AS collections,
          (SELECT count(*) FROM captures WHERE user_id = :u) AS captures,
          (SELECT max(received_at) FROM captures WHERE user_id = :u) AS last_capture_at
    """), {"u": user.id}).one()._asdict()
    return {**row, **extra}


@router.get("/settings", response_model=dict[str, Any])
def get_settings_(user: User = Depends(current_user), session: Session = Depends(get_session)):
    return load_settings(session, user.id)


@router.patch("/settings", response_model=dict[str, Any])
def patch_settings(body: dict[str, Any], user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Change known settings only; each value must have its default's type."""
    for key, value in body.items():
        default = DEFAULT_SETTINGS.get(key)
        if key not in DEFAULT_SETTINGS:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown setting: {key}")
        number = isinstance(default, (int, float)) and not isinstance(default, bool)
        ok = (isinstance(value, (int, float)) and not isinstance(value, bool)) if number else isinstance(value, type(default))
        if not ok or (key == "page_size" and value not in PAGE_SIZES):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Invalid value for {key}")
        session.merge(Setting(user_id=user.id, key=key, value=value))
    session.commit()
    return load_settings(session, user.id)


@router.get("/keybindings", response_model=dict[str, str])
def get_keybindings(user: User = Depends(current_user), session: Session = Depends(get_session)):
    """The user's remapped keys (action -> keys). Actions not listed keep the
    web app's defaults."""
    return {k.action: k.keys for k in session.scalars(select(Keybinding).where(Keybinding.user_id == user.id))}


@router.put("/keybindings", response_model=dict[str, str])
def put_keybindings(body: dict[str, str], user: User = Depends(current_user), session: Session = Depends(get_session)):
    session.execute(delete(Keybinding).where(Keybinding.user_id == user.id))
    session.add_all(Keybinding(user_id=user.id, action=action, keys=keys) for action, keys in body.items())
    session.commit()
    return body
