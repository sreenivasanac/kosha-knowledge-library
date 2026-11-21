"""Capture pipeline: store the envelope untouched, parse it, merge into items.

Re-ingesting is idempotent. When a post arrives again (same route or the other
one), the richer payload wins field by field: a new value replaces an old one
only when it carries something, text never gets shorter, and nothing a user or
a worker added (stars, notes, local files, extracts) is touched.
"""
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from kosha.models import (
    Capture,
    Collection,
    DeletedItem,
    Item,
    ItemCollection,
    ItemLink,
    ItemMedia,
    ItemRelation,
)
from kosha.parsers import ParsedItem, ParseResult, UnknownCapture, parse_capture
from kosha.parsers.common import order_content_types

LONGER_WINS = ("title", "body")


@dataclass
class Counts:
    new: int = 0
    updated: int = 0
    unchanged: int = 0


def compute_content_hash(title: str | None, body: str | None, extracts: list[str]) -> str:
    """Changes when the text or any media extract changes, so embeddings,
    summaries and cached Jev answers know to refresh."""
    return hashlib.sha256("\x1f".join([title or "", body or "", *extracts]).encode()).hexdigest()


def _set(obj: Any, name: str, value: Any) -> bool:
    if getattr(obj, name) == value:
        return False
    setattr(obj, name, value)
    return True


def _merge_fields(item: Item, parsed: ParsedItem) -> bool:
    changed = False
    for name, value in parsed.fields.items():
        if value is None or value == "":
            continue  # an empty field never erases what is held
        old = getattr(item, name)
        if name in LONGER_WINS and old and len(old) > len(value):
            continue
        changed |= _set(item, name, value)
    # A post that was a tombstone and now arrives whole is available again.
    if "unavailable_reason" not in parsed.fields and parsed.fields.get("author_handle"):
        changed |= _set(item, "unavailable_reason", None)
    changed |= _set(item, "is_saved", item.is_saved or parsed.is_saved)
    for name in ("saved_order", "saved_at", "saved_at_est"):
        if getattr(parsed, name) is not None:
            changed |= _set(item, name, getattr(parsed, name))
    if parsed.content_types:
        changed |= _set(item, "content_types", order_content_types(set(item.content_types or []) | set(parsed.content_types)))
    if parsed.raw is not None and (item.raw or {}).get(parsed.raw_format) != parsed.raw:
        item.raw = {**(item.raw or {}), parsed.raw_format: parsed.raw}
        changed = True
    return changed


def _merge_children(session: Session, item: Item, parsed: ParsedItem, collections: dict[str, Collection]) -> bool:
    """Media, links, relations and folder membership. Rows are matched on their
    natural key; new non-empty values win; rows absent from this payload stay."""
    changed = False
    for model, rows, key in ((ItemMedia, parsed.media, "source_key"), (ItemLink, parsed.links, "url")):
        if not rows:
            continue
        held = {getattr(r, key): r for r in session.scalars(select(model).where(model.item_id == item.id))}
        for row in rows:
            existing = held.get(row[key])
            if existing is None:
                session.add(model(item_id=item.id, **row))
                changed = True
            else:
                for name, value in row.items():
                    if value is not None:
                        changed |= _set(existing, name, value)

    if parsed.relations:
        held = {(r.relation, r.to_source_id): r
                for r in session.scalars(select(ItemRelation).where(ItemRelation.from_item_id == item.id))}
        for rel in parsed.relations:
            # "held" is only ever written by `_resolve_relations`, once the
            # target row really exists.
            state = "unavailable" if rel["target_state"] == "unavailable" else "missing"
            existing = held.get((rel["relation"], rel["to_source_id"]))
            if existing is None:
                session.add(ItemRelation(from_item_id=item.id, relation=rel["relation"],
                                         to_source_id=rel["to_source_id"], position=rel.get("position"),
                                         target_state=state))
                changed = True
            elif state == "unavailable" and existing.target_state == "missing":
                existing.target_state = "unavailable"
                changed = True

    for membership in parsed.collections:
        collection = collections[membership["source_id"]]
        existing = session.get(ItemCollection, (item.id, collection.id))
        if existing is None:
            session.add(ItemCollection(item_id=item.id, collection_id=collection.id, position=membership.get("position")))
            changed = True
        elif membership.get("position") is not None:
            changed |= _set(existing, "position", membership["position"])
    return changed


def _resolve_relations(session: Session, item_ids: list[int]) -> None:
    """Point relations at their target wherever the target row is now held:
    both the relations of the items just written and older relations that were
    waiting for one of them (a quoted post that arrives later, for example)."""
    if not item_ids:
        return
    session.flush()
    session.execute(text("""
        UPDATE item_relations r
        SET to_item_id = t.id,
            target_state = CASE WHEN t.unavailable_reason IS NULL THEN 'held' ELSE 'unavailable' END
        FROM items f, items t
        WHERE f.id = r.from_item_id AND r.to_item_id IS NULL
          AND t.user_id = f.user_id AND t.source = f.source AND t.source_id = r.to_source_id
          AND (f.id = ANY(:ids) OR t.id = ANY(:ids))
    """), {"ids": item_ids})


def upsert_result(session: Session, user_id: int, result: ParseResult) -> Counts:
    counts = Counts()
    collections: dict[str, Collection] = {}
    for c in result.collections:
        row = session.scalar(select(Collection).where(
            Collection.user_id == user_id, Collection.source == c["source"], Collection.source_id == c["source_id"]))
        if row is None:
            row = Collection(user_id=user_id, **c)
            session.add(row)
        elif c["name"] and c["name"] != c["source_id"]:
            row.name = c["name"]
        collections[c["source_id"]] = row
    session.flush()

    by_source: dict[str, list[ParsedItem]] = {}
    for parsed in result.items:
        by_source.setdefault(parsed.source, []).append(parsed)
    touched: list[int] = []
    for source, parsed_items in by_source.items():
        ids = [p.source_id for p in parsed_items]
        deleted = set(session.scalars(select(DeletedItem.source_id).where(
            DeletedItem.user_id == user_id, DeletedItem.source == source, DeletedItem.source_id.in_(ids))))
        held = {i.source_id: i for i in session.scalars(select(Item).where(
            Item.user_id == user_id, Item.source == source, Item.source_id.in_(ids)))}
        # Sources list newest first. Writing oldest first gives newer posts the
        # higher `seq`, which orders items that have no `saved_order`.
        for parsed in reversed(parsed_items):
            if parsed.source_id in deleted:
                continue  # hard-deleted by the user: a sync must not bring it back
            item = held.get(parsed.source_id)
            is_new = item is None
            if is_new:
                item = Item(user_id=user_id, source=source, source_id=parsed.source_id,
                            is_saved=parsed.is_saved, content_types=[])
                session.add(item)
            changed = _merge_fields(item, parsed)
            session.flush()
            changed |= _merge_children(session, item, parsed, collections)
            extracts = list(session.scalars(select(ItemMedia.extract_text).where(
                ItemMedia.item_id == item.id, ItemMedia.extract_text.is_not(None)).order_by(ItemMedia.position)))
            item.content_hash = compute_content_hash(item.title, item.body, extracts)
            touched.append(item.id)
            if parsed.is_saved:  # context items (quoted posts, parents) are not counted
                if is_new:
                    counts.new += 1
                elif changed:
                    counts.updated += 1
                else:
                    counts.unchanged += 1
    _resolve_relations(session, touched)
    return counts


def store_capture(session: Session, user_id: int, envelope: dict) -> Capture:
    """Keep the envelope untouched; the same batch posted twice reuses its row."""
    source, kind, payload = envelope["source"], envelope["kind"], envelope.get("payload")
    payload_hash = hashlib.sha256(json.dumps([source, kind, payload], sort_keys=True).encode()).hexdigest()
    capture = session.scalar(select(Capture).where(Capture.user_id == user_id, Capture.payload_hash == payload_hash))
    if capture is None:
        capture = Capture(user_id=user_id, source=source, kind=kind, meta=envelope.get("meta"),
                          payload=payload, payload_hash=payload_hash)
        session.add(capture)
        session.flush()
    return capture


def ingest_envelope(session: Session, user_id: int, envelope: dict) -> Capture:
    """Store one capture envelope (reusing the row if this exact batch was seen
    before), then parse and merge it. A parser or merge failure leaves the
    capture stored with `parse_error` set and no item changes, so it can be
    re-parsed after a fix; callers check `capture.parse_error`. An unknown
    source/kind raises `UnknownCapture` and stores nothing."""
    capture = store_capture(session, user_id, envelope)
    try:
        result = parse_capture(envelope["source"], envelope["kind"], envelope.get("payload"), envelope.get("meta"))
        with session.begin_nested():
            counts = upsert_result(session, user_id, result)
    except UnknownCapture:
        raise
    except Exception as exc:
        capture.parse_error = f"{type(exc).__name__}: {exc}"[:2000]
        return capture
    capture.parse_error = None
    capture.parsed_at = datetime.now(UTC)
    capture.items_new, capture.items_updated, capture.items_unchanged = counts.new, counts.updated, counts.unchanged
    return capture


def ingest_dir(session: Session, user_id: int, path: Path) -> dict:
    """Ingest every envelope file in a folder, in file-name (capture) order.
    A file that fails is reported and skipped; the rest still load."""
    totals = {"files": 0, "skipped": 0, "failed": [], "items_new": 0, "items_updated": 0, "items_unchanged": 0}
    for file in sorted(path.glob("*.json")):
        try:
            capture = ingest_envelope(session, user_id, json.loads(file.read_text()))
            session.commit()
            error = capture.parse_error
        except UnknownCapture:  # e.g. the extension's connection test ("ping")
            session.rollback()
            totals["skipped"] += 1
            continue
        except Exception as exc:  # bad or half-written file
            session.rollback()
            error = f"{type(exc).__name__}: {exc}"
        if error:
            totals["failed"].append(f"{file.name}: {error}"[:300])
            continue
        totals["files"] += 1
        for name in ("items_new", "items_updated", "items_unchanged"):
            totals[name] += getattr(capture, name)
    return totals
