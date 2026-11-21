"""Turn Item rows into API dictionaries, loading everything a card shows
(media, links, tags, folders, quoted post, reply and thread info) in a fixed
number of queries per page rather than per item."""
from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from kosha.config import DEFAULT_NAMESPACE
from kosha.models import Collection, Item, ItemCollection, ItemLink, ItemMedia, ItemRelation, ItemTag, Tag


def media_dict(m: ItemMedia, with_extract: bool = False) -> dict:
    return {
        "id": m.id, "type": m.type, "position": m.position,
        "url": f"/media/{m.id}",
        "preview_url": f"/media/{m.id}?preview=1" if (m.preview_url or m.local_preview_path) else None,
        "remote_url": m.original_url, "stored": bool(m.local_path),
        "mime_type": m.mime_type, "width": m.width, "height": m.height,
        "duration_ms": m.duration_ms, "alt_text": m.alt_text, "variants": m.variants,
        "extract_status": m.extract_status, "extract_kind": m.extract_kind,
        "extract_text": m.extract_text if with_extract else None,
    }


def tag_names(session: Session, user_id: int) -> dict[int, Tag]:
    """All of a user's tags by id (hundreds at most), for breadcrumbs."""
    return {t.id: t for t in session.scalars(select(Tag).where(Tag.user_id == user_id))}


def breadcrumb(tag: Tag, tags: dict[int, Tag]) -> list[str]:
    return [tags[int(i)].name for i in str(tag.path).split(".") if int(i) in tags]


def serialize_items(session: Session, user_id: int, items: list[Item], nest_quoted: bool = True, detail: bool = False) -> list[dict]:
    if not items:
        return []
    ids = [i.id for i in items]

    def grouped(model, key="item_id", order=None):
        rows = defaultdict(list)
        stmt = select(model).where(getattr(model, key).in_(ids))
        for row in session.scalars(stmt.order_by(order) if order is not None else stmt):
            rows[getattr(row, key)].append(row)
        return rows

    media = grouped(ItemMedia, order=ItemMedia.position)
    links = grouped(ItemLink, order=ItemLink.position)
    relations = grouped(ItemRelation, key="from_item_id")

    tags = tag_names(session, user_id)
    item_tags = defaultdict(list)
    role_order = {"primary": 0, "secondary": 1, "other": 2}
    for it in session.scalars(select(ItemTag).where(ItemTag.item_id.in_(ids), ~ItemTag.removed_by_user,
                                                 ItemTag.namespace == DEFAULT_NAMESPACE)):
        tag = tags.get(it.tag_id)
        if tag:
            item_tags[it.item_id].append({
                "tag_id": tag.id, "name": tag.name, "path": breadcrumb(tag, tags), "path_ids": str(tag.path),
                "role": it.role, "status": tag.status, "color": tag.color,
                "attached_by": it.attached_by, "confidence": it.confidence,
            })
    for rows in item_tags.values():
        rows.sort(key=lambda r: (role_order[r["role"]], r["path"]))

    collections = defaultdict(list)
    for item_id, c in session.execute(
        select(ItemCollection.item_id, Collection).join(Collection, Collection.id == ItemCollection.collection_id)
        .where(ItemCollection.item_id.in_(ids))
    ):
        collections[item_id].append({"id": c.id, "name": c.name, "kind": c.kind})

    # Targets of quotes and replies that are held.
    target_ids = {r.to_item_id for rows in relations.values() for r in rows if r.to_item_id}
    targets = {t.id: t for t in session.scalars(select(Item).where(Item.id.in_(target_ids)))} if target_ids else {}
    quoted_out = {}
    if nest_quoted:
        quoted_rows = [targets[r.to_item_id] for rows in relations.values() for r in rows
                       if r.relation == "quotes" and r.to_item_id in targets]
        quoted_out = {q["id"]: q for q in serialize_items(session, user_id, list({q.id: q for q in quoted_rows}.values()), nest_quoted=False)}

    # Thread sizes: members point at the root's source id.
    roots = {r.to_source_id for rows in relations.values() for r in rows if r.relation == "thread_member"}
    roots |= {i.source_id for i in items}
    thread_sizes = dict(session.execute(
        select(ItemRelation.to_source_id, func.count())
        .join(Item, Item.id == ItemRelation.from_item_id)
        .where(Item.user_id == user_id, ItemRelation.relation == "thread_member", ItemRelation.to_source_id.in_(roots))
        .group_by(ItemRelation.to_source_id)
    ).all())

    def pointer(rel: ItemRelation | None) -> dict | None:
        if rel is None:
            return None
        target = targets.get(rel.to_item_id)
        return {"source_id": rel.to_source_id, "item_id": rel.to_item_id, "target_state": rel.target_state,
                "author_handle": target.author_handle if target else None}

    out = []
    # LinkedIn only: the author's headline, kept in the raw page capture (`raw` itself is not loaded).
    headlines = dict(session.execute(select(Item.id, Item.raw["page"]["author_headline"].as_string()).where(
        Item.id.in_([i.id for i in items if i.source == "linkedin"]))).all())
    for i in items:
        rels = {r.relation: r for r in relations.get(i.id, [])}
        quote = rels.get("quotes")
        root = rels["thread_member"].to_source_id if "thread_member" in rels else i.source_id
        out.append({
            "id": i.id, "seq": i.seq, "source": i.source, "source_id": i.source_id, "is_saved": i.is_saved,
            "url": i.url, "title": i.title, "body": i.body, "summary": i.summary,
            "author": {"name": i.author_name, "handle": i.author_handle, "avatar": i.author_avatar,
                       "headline": headlines.get(i.id)},
            "posted_at": i.posted_at,
            "saved_at": i.saved_at or i.saved_at_est,
            "saved_at_approximate": i.saved_at is None and i.saved_at_est is not None,
            "saved_order": i.saved_order, "captured_at": i.captured_at,
            "content_types": i.content_types or [], "lang": i.lang,
            "likes": i.likes, "reposts": i.reposts, "replies": i.replies, "views": i.views,
            "duration_s": i.duration_s, "starred": i.starred, "note": i.note,
            "read": i.read_at is not None, "read_at": i.read_at,
            "archived": i.archived_at is not None, "archived_at": i.archived_at,
            "unavailable_reason": i.unavailable_reason,
            "media": [media_dict(m, with_extract=detail) for m in media.get(i.id, [])],
            "links": [{"url": l.url, "domain": l.domain, "title": l.title, "description": l.description,
                       "image_url": l.image_url} for l in links.get(i.id, [])],
            "tags": item_tags.get(i.id, []),
            "collections": collections.get(i.id, []),
            "quote": pointer(quote),
            "quoted": quoted_out.get(quote.to_item_id) if quote else None,
            "reply_to": pointer(rels.get("replies_to")),
            "thread": {"root_source_id": root, "member_count": thread_sizes[root]} if root in thread_sizes else None,
        })
    return out


def thread_items(session: Session, user_id: int, item: Item) -> list[Item]:
    """Every held post of the thread `item` belongs to (root included), oldest
    first; empty when the item is not part of a thread."""
    root = session.scalar(select(ItemRelation.to_source_id).where(
        ItemRelation.from_item_id == item.id, ItemRelation.relation == "thread_member")) or item.source_id
    members = select(ItemRelation.from_item_id).where(
        ItemRelation.relation == "thread_member", ItemRelation.to_source_id == root)
    rows = session.scalars(
        select(Item).where(Item.user_id == user_id, Item.source == item.source,
                           (Item.id.in_(members)) | (Item.source_id == root))
        .order_by(Item.posted_at.asc().nulls_last(), Item.seq)
    ).all()
    return list(rows) if len(rows) > 1 else []
