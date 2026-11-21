"""Item list queries.

Two halves on purpose: `conditions()` turns the basic REST filters into SQL
conditions, and `fetch_page()` pages over any list of conditions. The qualifier
language (SEARCH_DESIGN.md B.4) will compile its filter AST into the same kind
of condition list and call `fetch_page()` unchanged.
"""
from pydantic import BaseModel
from sqlalchemy import ColumnElement, exists, extract, func, select
from sqlalchemy.orm import Session

from kosha.models import Item, ItemCollection, ItemTag, Tag

# Newest saved first. Items the source gave no order for (official-API and
# LinkedIn captures) come first, newest ingested on top: in practice they are
# the daily sync's new bookmarks, and they take their real place once the
# extension captures them with a `saved_order`. Neither column changes with
# user actions, so page N stays page N.
STABLE_ORDER = (Item.saved_order.desc().nulls_first(), Item.seq.desc())

# The year an item belongs to in the sidebar: when it was saved, as well as known.
ITEM_YEAR = extract("year", func.coalesce(Item.saved_at, Item.saved_at_est, Item.posted_at))


class ItemFilters(BaseModel):
    source: str | None = None
    is_saved: bool | None = True
    archived: bool | None = False
    starred: bool | None = None
    read: bool | None = None
    untagged: bool | None = None
    tag_id: int | None = None  # includes everything beneath the tag
    collection_id: int | None = None
    content_type: str | None = None
    author: str | None = None  # handle, with or without @
    year: int | None = None


def tagged_under(tag_id: int) -> ColumnElement[bool]:
    """Item carries the tag or any tag in its subtree."""
    root_path = select(Tag.path).where(Tag.id == tag_id).scalar_subquery()
    return exists().where(
        ItemTag.item_id == Item.id, ItemTag.tag_id == Tag.id,
        ~ItemTag.removed_by_user, Tag.path.op("<@")(root_path),
    )


def conditions(f: ItemFilters) -> list[ColumnElement[bool]]:
    where: list[ColumnElement[bool]] = []
    if f.source:
        where.append(Item.source == f.source)
    if f.is_saved is not None:
        where.append(Item.is_saved == f.is_saved)
    if f.archived is not None:
        where.append(Item.archived_at.is_not(None) if f.archived else Item.archived_at.is_(None))
    if f.starred is not None:
        where.append(Item.starred == f.starred)
    if f.read is not None:
        where.append(Item.read_at.is_not(None) if f.read else Item.read_at.is_(None))
    if f.untagged is not None:
        has_tag = exists().where(ItemTag.item_id == Item.id, ~ItemTag.removed_by_user)
        where.append(~has_tag if f.untagged else has_tag)
    if f.tag_id is not None:
        where.append(tagged_under(f.tag_id))
    if f.collection_id is not None:
        where.append(exists().where(ItemCollection.item_id == Item.id, ItemCollection.collection_id == f.collection_id))
    if f.content_type:
        where.append(Item.content_types.contains([f.content_type]))
    if f.author:
        where.append(func.lower(Item.author_handle) == f.author.lstrip("@").lower())
    if f.year is not None:
        where.append(ITEM_YEAR == f.year)
    return where


def fetch_page(session: Session, user_id: int, where: list[ColumnElement[bool]], page: int, page_size: int) -> tuple[list[Item], int]:
    """One numbered page in the stable order, plus the total count."""
    scope = [Item.user_id == user_id, *where]
    total = session.scalar(select(func.count()).select_from(Item).where(*scope)) or 0
    items = session.scalars(
        select(Item).where(*scope).order_by(*STABLE_ORDER).limit(page_size).offset((page - 1) * page_size)
    ).all()
    return list(items), total
