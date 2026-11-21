"""Pipeline writes (SEARCH_DESIGN.md E.4): tag rows, roles, item state, lists.

Safety rules kept here:
- A write of more than WRITE_DIRECT_MAX items is only applied with a confirm
  token that was issued for exactly that write on exactly that set.
- A tag an agent creates is `proposed`; so is an agent's tag that receives
  more than WRITE_PROPOSE_ABOVE items in one write.
- Rows written here carry `attached_by='agent'` and Jev's p as `confidence`.
- A tag the user removed from an item is never put back, and a row attached by
  hand is never changed or removed.
- Every write stores the prior state as one undo batch (`POST /api/undo/{id}`).
"""
import hashlib
import hmac
import time
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from kosha.api.tags import slugify
from kosha.config import get_settings
from kosha.models import Item, ItemTag, List, ListItem, Tag, UndoBatch, tags_id_seq

# --- Tunables ---------------------------------------------------------------
WRITE_DIRECT_MAX = 25  # up to this many items a write applies at once
WRITE_PROPOSE_ABOVE = 500  # above this an agent's tag goes back to `proposed`
CONFIRM_TTL_S = 600
AGENT = "agent"
ROLES = ("primary", "secondary", "other")
ITEM_TAG_COLUMNS = ("item_id", "tag_id", "namespace", "role", "attached_by", "confidence", "removed_by_user")
INSERT_CHUNK = 5000


# --- Confirm tokens ---------------------------------------------------------
def digest_of(*parts) -> str:
    """What a confirm token is bound to: the write as written and the exact set."""
    return hashlib.sha256("\x1f".join(str(p) for p in parts).encode()).hexdigest()


def _sign(user_id: int, digest: str, expires: int) -> str:
    key = get_settings().kosha_secret_key.encode()
    return hmac.new(key, f"{user_id}|{digest}|{expires}".encode(), hashlib.sha256).hexdigest()[:20]


def confirm_token(user_id: int, digest: str) -> str:
    """Stateless: the expiry in the clear, signed together with the user and the digest."""
    expires = int(time.time()) + CONFIRM_TTL_S
    return f"c_{expires:x}_{_sign(user_id, digest, expires)}"


def confirm_ok(user_id: int, digest: str, token: str | None) -> bool:
    try:
        _, expires, signature = (token or "").split("_")
        expires = int(expires, 16)
    except ValueError:
        return False
    return expires >= time.time() and hmac.compare_digest(signature, _sign(user_id, digest, expires))


# --- Tag paths --------------------------------------------------------------
def tag_paths(session: Session, user_id: int, namespace: str) -> dict[int, tuple[Tag, str]]:
    """Every tag of one namespace with its path of names ("AI/RAG")."""
    tags = {t.id: t for t in session.scalars(select(Tag).where(Tag.user_id == user_id, Tag.namespace == namespace))}
    return {t.id: (t, "/".join(tags[int(i)].name for i in str(t.path).split(".") if int(i) in tags)) for t in tags.values()}


def find_tag(session: Session, user_id: int, namespace: str, path: str) -> Tag | None:
    """The tag at this full path from the root; names compare without case."""
    wanted = "/".join(p.strip().lower() for p in path.strip("/#").split("/") if p.strip())
    for tag, tag_path in tag_paths(session, user_id, namespace).values():
        if tag_path.lower() == wanted:
            return tag
    return None


def create_tag_path(session: Session, user_id: int, namespace: str, path: str) -> Tag:
    """Create the tag and any missing parents, as an agent's proposal."""
    parent, walked = None, []
    for name in (p.strip() for p in path.strip("/#").split("/") if p.strip()):
        walked.append(name)
        tag = find_tag(session, user_id, namespace, "/".join(walked))
        if tag is None:
            tag_id = session.scalar(tags_id_seq)
            tag = Tag(id=tag_id, user_id=user_id, parent_id=parent.id if parent else None,
                      path=f"{parent.path}.{tag_id}" if parent else str(tag_id), name=name, slug=slugify(name),
                      namespace=namespace, status="proposed", origin=AGENT)
            session.add(tag)
            session.flush()
        parent = tag
    return parent


# --- Undo snapshots ---------------------------------------------------------
def _item_tag_rows(session: Session, ids: list[int], namespace: str) -> list[ItemTag]:
    return list(session.scalars(select(ItemTag).where(ItemTag.item_id == func.any(ids), ItemTag.namespace == namespace)))


def _save_batch(session: Session, user_id: int, before: dict) -> int:
    batch = UndoBatch(user_id=user_id, before={"items": [], "tag_ids": [], "item_tags": [], **before})
    session.add(batch)
    session.flush()
    return batch.id


def _tags_before(rows: list[ItemTag], ids: list[int], extra_tag_ids: list[int]) -> dict:
    """The tag rows of these items as they are now, in the shape the undo
    route restores: it deletes (items x tag_ids) and re-inserts `item_tags`."""
    return {"items": [{"id": i} for i in ids],
            "tag_ids": sorted({r.tag_id for r in rows} | set(extra_tag_ids)),
            "item_tags": [{c: getattr(r, c) for c in ITEM_TAG_COLUMNS} for r in rows]}


# --- Tag writes -------------------------------------------------------------
def classify(session: Session, ids: list[int], tag: Tag | None) -> dict:
    """How a `tag` write would land, without writing: new rows, rows already
    there (an agent's are refreshed, a hand-made one is left), and items the
    user removed this tag from (skipped for good)."""
    counts = {"new": len(ids), "already": 0, "by_hand": 0, "removed_by_you": 0}
    if tag is None:
        return counts
    for row in session.scalars(select(ItemTag).where(ItemTag.item_id == func.any(ids), ItemTag.tag_id == tag.id)):
        kind = "removed_by_you" if row.removed_by_user else ("already" if row.attached_by == AGENT else "by_hand")
        counts[kind] += 1
        counts["new"] -= 1
    return counts


def apply_tags(session: Session, user_id: int, namespace: str, plan: list[tuple[int, int, str | None, float | None]],
               auto_roles: bool = False) -> tuple[dict, int]:
    """Attach tags as an agent. `plan` rows are (item id, tag id, role,
    confidence); role None means "other" for a new row and no change for one
    already there. Returns (counts, undo batch id). The caller commits."""
    ids = sorted({p[0] for p in plan})
    rows = _item_tag_rows(session, ids, namespace)
    before = _tags_before(rows, ids, [p[1] for p in plan])
    by_key = {(r.item_id, r.tag_id): r for r in rows}
    holder = {(r.item_id, r.role): r for r in rows if r.role != "other" and not r.removed_by_user}
    counts = {"new": 0, "already": 0, "by_hand": 0, "removed_by_you": 0}
    inserts, set_role = [], []
    for item_id, tag_id, role, confidence in plan:
        row = by_key.get((item_id, tag_id))
        if row is not None and (row.removed_by_user or row.attached_by != AGENT):
            counts["removed_by_you" if row.removed_by_user else "by_hand"] += 1
            continue
        if role in ("primary", "secondary"):
            held = holder.get((item_id, role))
            if held is not None and held is not row:
                if held.attached_by != AGENT:
                    role = None  # a role set by hand stays where it is
                else:
                    held.role = "other"
        if row is None:
            counts["new"] += 1
            inserts.append({"item_id": item_id, "tag_id": tag_id, "namespace": namespace, "role": "other",
                            "attached_by": AGENT, "confidence": confidence})
        else:
            counts["already"] += 1
            row.confidence = confidence if confidence is not None else row.confidence
            if role is not None and row.role != role:
                row.role = "other"
        if role in ("primary", "secondary"):
            set_role.append((item_id, tag_id, role))
    session.flush()  # demotions land before any row takes a role (one primary per item)
    for start in range(0, len(inserts), INSERT_CHUNK):
        session.execute(insert(ItemTag).values(inserts[start:start + INSERT_CHUNK]))
    session.expire_all()
    if set_role:
        fresh = {(r.item_id, r.tag_id): r for r in _item_tag_rows(session, ids, namespace)}
        taken = set()
        for item_id, tag_id, role in set_role:
            if (item_id, role) not in taken:
                taken.add((item_id, role))
                fresh[(item_id, tag_id)].role = role
        session.flush()
    if auto_roles:
        _auto_roles(session, ids, namespace)
    return counts, _save_batch(session, user_id, before)


def remove_tags(session: Session, user_id: int, ids: list[int], tag: Tag) -> tuple[dict, int]:
    """Take a tag off where an agent attached it. Rows attached by hand stay."""
    rows = [r for r in _item_tag_rows(session, ids, tag.namespace) if r.tag_id == tag.id and not r.removed_by_user]
    mine = [r for r in rows if r.attached_by == AGENT]
    batch = _save_batch(session, user_id, _tags_before(mine, [r.item_id for r in mine], [tag.id]))
    if mine:
        session.execute(delete(ItemTag).where(ItemTag.tag_id == tag.id, ItemTag.item_id == func.any([r.item_id for r in mine])))
    return {"removed": len(mine), "by_hand": len(rows) - len(mine)}, batch


def _auto_roles(session: Session, ids: list[int], namespace: str) -> int:
    """Among the tags an agent attached to each item: the highest confidence
    becomes primary, the next secondary, the rest other. A tag and its own
    ancestor never both count (the deeper one wins). Roles set by hand stay.
    Returns how many rows changed role."""
    rows = [r for r in _item_tag_rows(session, ids, namespace) if not r.removed_by_user]
    paths = dict(session.execute(select(Tag.id, Tag.path).where(Tag.id.in_({r.tag_id for r in rows}))).all())
    by_item: dict[int, list[ItemTag]] = {}
    for row in rows:
        by_item.setdefault(row.item_id, []).append(row)
    wanted: dict[tuple[int, int], str] = {}
    for item_rows in by_item.values():
        hand_roles = {r.role for r in item_rows if r.attached_by != AGENT}
        agent = sorted((r for r in item_rows if r.attached_by == AGENT), key=lambda r: (-(r.confidence or 0), r.tag_id))
        free = [role for role in ("primary", "secondary") if role not in hand_roles]
        for row in agent:
            path = str(paths[row.tag_id])
            is_ancestor = any(str(paths[o.tag_id]).startswith(path + ".") for o in agent if o is not row)
            wanted[(row.item_id, row.tag_id)] = free.pop(0) if free and not is_ancestor else "other"
    changed = [r for r in rows if r.attached_by == AGENT and wanted[(r.item_id, r.tag_id)] != r.role]
    for row in changed:
        row.role = "other"
    session.flush()  # everyone steps down before anyone steps up (one primary per item)
    for row in changed:
        row.role = wanted[(row.item_id, row.tag_id)]
    session.flush()
    return len(changed)


def auto_roles(session: Session, user_id: int, ids: list[int], namespace: str) -> tuple[dict, int]:
    before = _tags_before(_item_tag_rows(session, ids, namespace), ids, [])
    changed = _auto_roles(session, ids, namespace)
    return {"roles_changed": changed}, _save_batch(session, user_id, before)


# --- Item state and lists ---------------------------------------------------
STATE_WRITES = {  # stage -> (column, value given `now`)
    "star": ("starred", lambda now: True), "unstar": ("starred", lambda now: False),
    "read": ("read_at", lambda now: now), "unread": ("read_at", lambda now: None),
    "archive": ("archived_at", lambda now: now), "unarchive": ("archived_at", lambda now: None),
}


def set_state(session: Session, user_id: int, ids: list[int], stage: str) -> tuple[dict, int]:
    """Star, read or archive the set. Read and archive keep the time first set."""
    column, value = STATE_WRITES[stage]
    now = datetime.now(UTC)
    items = session.scalars(select(Item).where(Item.user_id == user_id, Item.id == func.any(ids))).all()
    before = {"items": [{"id": i.id, "starred": i.starred, "note": i.note,
                         "read_at": i.read_at.isoformat() if i.read_at else None,
                         "archived_at": i.archived_at.isoformat() if i.archived_at else None} for i in items]}
    changed = 0
    for item in items:
        new = value(now)
        if column != "starred" and new is not None and getattr(item, column) is not None:
            continue
        if getattr(item, column) != new:
            setattr(item, column, new)
            changed += 1
    return {"changed": changed}, _save_batch(session, user_id, before)


def add_to_list(session: Session, user_id: int, ids: list[int], name: str) -> tuple[dict, int]:
    """Append the set to a hand-made list, in set order; the list is created if missing."""
    row = session.scalar(select(List).where(List.user_id == user_id, func.lower(List.name) == name.lower()))
    if row is None:
        row = List(user_id=user_id, name=name)
        session.add(row)
        session.flush()
    present = set(session.scalars(select(ListItem.item_id).where(ListItem.list_id == row.id)))
    position = session.scalar(select(func.coalesce(func.max(ListItem.position), 0)).where(ListItem.list_id == row.id))
    added = [i for i in dict.fromkeys(ids) if i not in present]
    session.add_all(ListItem(list_id=row.id, item_id=item_id, position=position + n) for n, item_id in enumerate(added, start=1))
    session.flush()
    return {"added": len(added), "already": len(ids) - len(added)}, _save_batch(session, user_id, {"list": {"id": row.id, "added": added}})
