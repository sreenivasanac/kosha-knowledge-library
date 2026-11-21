"""Demo mode (KOSHA_DEMO=true): a separate public instance with one published
account, where visitors can do everything that is free and reversible.

Two halves:
- What a demo refuses, enforced in one place: `DemoGuard` (an allow-list of
  the requests that change something, a short list of refused reads, a write
  limit per client address) and `check_stages` (the paid pipeline stages).
- The seed bundle the demo's database is built from and reset to every night:
  `export_bundle`, `load_bundle`, `reset` (the `kosha demo-*` commands; docs/DEMO.md).
"""
import json
import re
import time
from collections import Counter, defaultdict, deque
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import HTTPException, status
from sqlalchemy import func, insert, select, text
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse

from kosha import storage
from kosha.config import API_PREFIX, DEFAULT_NAMESPACE, DEMO_MEDIA_SUBDIR, DEMO_WRITES_PER_MINUTE, MCP_PATH, get_settings
from kosha.items_query import STABLE_ORDER
from kosha.models import (
    Base,
    DemoQueryVector,
    Item,
    ItemEmbedding,
    ItemLink,
    ItemMedia,
    ItemRelation,
    ItemTag,
    Tag,
    User,
)

REFUSAL = {"message": "Not available in the demo", "code": "demo"}

# --- What a demo allows ------------------------------------------------------
# Every request that is not GET/HEAD/OPTIONS is refused unless it is listed
# here, so a route added later is closed in the demo until someone opens it.
# Not listed, and so refused: setup, sign-up, password change and reset, API
# tokens, captures, imports, adding items, media-text start, Ask, the Jev
# check, permanent delete, removals, sync, and everything under /admin.
_A = re.escape(API_PREFIX)
ALLOWED_WRITES = [(methods.split("|"), re.compile(_A + pattern)) for methods, pattern in (
    ("POST", r"/auth/(login|logout)"),
    ("PATCH", r"/items/\d+"),  # star, read, archive, note
    ("POST", r"/items/bulk"),
    ("POST", r"/undo/\d+"),
    ("PUT|DELETE", r"/items/\d+/tags/\d+"),
    ("POST|PATCH|DELETE", r"/tags(/\d+(/(move|merge|approve|reject))?)?"),
    ("POST|PATCH|PUT|DELETE", r"/lists(/\d+(/(items|order))?)?"),
    ("POST|PATCH|DELETE", r"/saved-views(/\d+)?"),
    ("POST", r"/search/parse"),
    ("PATCH", r"/settings"),
    ("PUT", r"/keybindings"),
    ("POST", r"/query"),  # minus the stages in BLOCKED_STAGES
    ("PATCH", r"/onboarding"),
    ("POST", r"/onboarding/complete"),
    ("POST", r"/media-text/estimate"),  # reads only
)]
# Refused whatever the method.
REFUSED_PATHS = re.compile(rf"({_A}/admin|{_A}/removals|{re.escape(MCP_PATH)})(/.*)?")
# Pipeline stages that call a paid service (Jev, the Voyage reranker, embedding of tag definitions).
BLOCKED_STAGES = ("jev", "score_labels", "rerank")
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


def refused(method: str, path: str, bearer: bool = False, query: str = "") -> bool:
    """Whether the demo refuses this request."""
    if bearer or REFUSED_PATHS.fullmatch(path):  # no API tokens in a demo, so no bearer auth and no MCP
        return True
    if method in SAFE_METHODS:
        # `rerank=voyage|jev` on a search is a paid second stage.
        return path == f"{API_PREFIX}/items" and parse_qs(query).get("rerank", ["none"])[-1] != "none"
    return not any(method in methods and pattern.fullmatch(path) for methods, pattern in ALLOWED_WRITES)


def check_stages(stages) -> None:
    """Called by `run_query` for every pipeline, whoever sent it."""
    if get_settings().kosha_demo and any(stage.name in BLOCKED_STAGES for stage in stages):
        raise HTTPException(status.HTTP_403_FORBIDDEN, REFUSAL)


_writes: dict[str, deque] = defaultdict(deque)  # client address -> times of its recent changing requests


class DemoGuard:
    """ASGI middleware, a no-op unless KOSHA_DEMO is on. Refusals are
    403 `{detail: {message, code: "demo"}}`."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not get_settings().kosha_demo:
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        bearer = headers.get(b"authorization", b"").lower().startswith(b"bearer ")
        if refused(scope["method"], scope["path"], bearer, scope.get("query_string", b"").decode("latin-1")):
            return await JSONResponse({"detail": REFUSAL}, status.HTTP_403_FORBIDDEN)(scope, receive, send)
        if scope["method"] not in SAFE_METHODS:
            # ponytail: in memory and per process, like the login limiter; add `limit_req` in nginx too (docs/DEMO.md).
            recent, now = _writes[(scope.get("client") or ("unknown",))[0]], time.monotonic()
            while recent and now - recent[0] > 60:
                recent.popleft()
            if len(recent) >= DEMO_WRITES_PER_MINUTE:
                return await JSONResponse({"detail": "Too many changes from this address; wait a minute"},
                                          status.HTTP_429_TOO_MANY_REQUESTS)(scope, receive, send)
            recent.append(now)
        await self.app(scope, receive, send)


# --- The seed bundle ---------------------------------------------------------
BUNDLE_FILE = "bundle.json"
SUMMARY_FILE = "SUMMARY.txt"
BUNDLE_MEDIA_DIR = "media"
BUNDLE_VERSION = 1
SUMMARY_TOP_AUTHORS = 60
# What leaves the source library. Deliberately absent from items: note,
# starred, read_at, archived_at and raw (the captured payload); the search
# columns are rebuilt on load. Nothing else of the user is exported: no
# captures, Jev answers, lists, views, settings, spend or tags outside --tags.
ITEM_COLUMNS = ("id", "seq", "source", "source_id", "is_saved", "saved_order", "saved_at", "saved_at_est", "captured_at",
                "content_types", "url", "title", "body", "author_name", "author_handle", "author_avatar", "posted_at", "lang",
                "likes", "reposts", "replies", "views", "duration_s", "summary", "summary_method", "summary_status",
                "content_hash", "unavailable_reason")
MEDIA_COLUMNS = ("id", "item_id", "source_key", "type", "mime_type", "position", "original_url", "preview_url", "variants",
                 "local_path", "local_preview_path", "bytes", "duration_ms", "width", "height", "alt_text", "extract_text",
                 "extract_kind", "extract_format", "extract_status", "extract_model", "extract_lang")
LINK_COLUMNS = ("item_id", "url", "domain", "title", "description", "image_url", "position")
RELATION_COLUMNS = ("from_item_id", "relation", "to_item_id", "to_source_id", "position", "target_state")
TAG_COLUMNS = ("id", "parent_id", "path", "name", "slug", "description", "status", "origin", "namespace", "color", "sort")
ITEM_TAG_COLUMNS = ("item_id", "tag_id", "namespace", "role", "attached_by", "confidence")
DATETIME_COLUMNS = ("saved_at", "saved_at_est", "captured_at", "posted_at")
CONTEXT_RELATIONS = ("quotes", "replies_to")  # the posts an exported item quotes or answers come along
# Sequences to move past the loaded ids, so rows the demo's visitors add do not collide.
SEQUENCES = (("items_seq", "items", "seq"), ("tags_id_seq", "tags", "id"))
SERIAL_TABLES = ("items", "item_media")
RESET_LOCK_TIMEOUT_S = 30


class BundleError(RuntimeError):
    pass


def floats(vector) -> list[float]:
    """A stored vector as a plain list (pgvector hands back its own type)."""
    return vector.to_list() if hasattr(vector, "to_list") else list(vector)


def _rows(objects, columns) -> list[dict]:
    def plain(value):
        return value.isoformat() if isinstance(value, datetime) else (str(value) if value is not None and not isinstance(
            value, (str, int, float, bool, list, dict)) else value)

    return [{c: plain(getattr(o, c)) for c in columns} for o in objects]


def export_bundle(session: Session, email: str, tag_paths: list[str], limit: int, out: Path,
                  queries_file: Path | None = None, sources: list[str] | None = None,
                  exclude_tag_paths: list[str] | None = None, spread: bool = False) -> str:
    """Write a seed bundle from one user's library: the newest `limit` saved
    items that carry a tag at or beneath one of `tag_paths`, with the posts
    they quote or answer, their media rows and stored files, links,
    embeddings, and only the tags inside those subtrees (plus the parents that
    lead to them). `sources` keeps only items of those sources;
    `exclude_tag_paths` leaves out every item that carries a tag at or beneath
    one of them, in any role and whoever attached it; `spread` takes the items
    from the named tags in turn (newest first within each) instead of the
    newest overall. Archived items and items with no text never leave.
    Returns the summary, which is also written to SUMMARY.txt: read it before
    publishing."""
    from kosha.pipeline import writes  # here: writes imports the API modules

    user = session.scalar(select(User).where(func.lower(User.email) == email.strip().lower()))
    if user is None:
        raise BundleError(f"No user {email}")
    # The allowed tags: each named tag with everything beneath it, except rejected tags (and what is under
    # them). Proposed tags go too: a library whose tags were never approved one by one would export none.
    def named(paths: list[str]) -> list[Tag]:
        found = []
        for path in paths:
            tag = writes.find_tag(session, user.id, DEFAULT_NAMESPACE, path)
            if tag is None:
                raise BundleError(f'No tag at "{path}"')
            found.append(tag)
        return found

    roots, barred_roots = named(tag_paths), named(exclude_tag_paths or [])
    names = writes.tag_paths(session, user.id, DEFAULT_NAMESPACE)
    within = lambda tag, tops: any(str(tag.path) == str(r.path) or str(tag.path).startswith(f"{r.path}.") for r in tops)  # noqa: E731
    inside = lambda tag: within(tag, roots)  # noqa: E731
    # The excluded subtrees whole, whatever a tag's status: a proposed tag marks the item too.
    barred = [tag.id for tag, _ in names.values() if within(tag, barred_roots)]
    allowed = {r.id for r in roots}
    for tag, _ in sorted(names.values(), key=lambda pair: str(pair[0].path).count(".")):  # parents first: a tag needs its parent exported
        if tag.status != "rejected" and inside(tag) and tag.parent_id in allowed:
            allowed.add(tag.id)
    # Parents above the named tags are exported as bare tree nodes, without their own items.
    structure = allowed | {int(i) for r in roots for i in str(r.path).split(".")}

    # What may leave at all, saved item or context: this user's, not archived, with some text, of the wanted
    # sources, and carrying no excluded tag.
    leaves = [Item.user_id == user.id, Item.archived_at.is_(None),
              func.btrim(func.coalesce(Item.title, "") + func.coalesce(Item.body, "")) != "",
              Item.id.not_in(select(ItemTag.item_id).where(ItemTag.tag_id.in_(barred), ~ItemTag.removed_by_user))]
    if sources:
        leaves.append(Item.source.in_(sources))

    def newest(tag_ids) -> list[Item]:
        tagged = select(ItemTag.item_id).where(ItemTag.tag_id.in_(tag_ids), ~ItemTag.removed_by_user, ItemTag.namespace == DEFAULT_NAMESPACE)
        return list(session.scalars(select(Item).where(*leaves, Item.is_saved, Item.id.in_(tagged)).order_by(*STABLE_ORDER).limit(limit)))

    if spread:
        # One item from each named tag in turn, so a busy tag does not fill the bundle alone.
        queues = [newest([i for i in allowed if within(names[i][0], [root])]) for root in roots]
        picked: dict[int, Item] = {}
        while len(picked) < limit and any(queues):
            for queue in queues:
                while queue and queue[0].id in picked:
                    queue.pop(0)
                if queue and len(picked) < limit:
                    item = queue.pop(0)
                    picked[item.id] = item
        items = list(picked.values())
    else:
        items = newest(allowed)
    ids = [i.id for i in items]
    # Only the posts an exported item quotes or answers come along.
    context = list(session.scalars(select(Item).where(
        *leaves, ~Item.is_saved, Item.id.in_(select(ItemRelation.to_item_id).where(
            ItemRelation.from_item_id.in_(ids), ItemRelation.relation.in_(CONTEXT_RELATIONS))))))
    every = ids + [i.id for i in context]
    held = set(every)

    relations = _rows(session.scalars(select(ItemRelation).where(ItemRelation.from_item_id.in_(every))), RELATION_COLUMNS)
    for row in relations:  # a target that stays behind is "missing" in the demo
        if row["to_item_id"] not in held:
            row["to_item_id"], row["target_state"] = None, ("unavailable" if row["target_state"] == "unavailable" else "missing")
    media = list(session.scalars(select(ItemMedia).where(ItemMedia.item_id.in_(every)).order_by(ItemMedia.id)))
    item_tags = list(session.scalars(select(ItemTag).where(
        ItemTag.item_id.in_(ids), ItemTag.tag_id.in_(allowed), ~ItemTag.removed_by_user, ItemTag.namespace == DEFAULT_NAMESPACE)))
    embeddings = [{"item_id": e.item_id, "model": e.model, "content_hash": e.content_hash, "vector": floats(e.vector)}
                  for e in session.scalars(select(ItemEmbedding).where(ItemEmbedding.item_id.in_(every)))]

    # Stored media files, copied out of the media storage under their own keys.
    out.mkdir(parents=True, exist_ok=True)
    store, copied, copied_bytes = storage.get_storage(session), 0, 0
    for m in media:
        for key in filter(None, (m.local_path, m.local_preview_path)):
            target = out / BUNDLE_MEDIA_DIR / key
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                store.download(key, target)
            except storage.ERRORS:  # the row names a file the storage no longer has
                continue
            copied, copied_bytes = copied + 1, copied_bytes + target.stat().st_size

    # Vectors of the suggested searches (one line each), so the demo can rank by meaning without calling Voyage.
    vectors = []
    queries = [line.strip() for line in queries_file.read_text().splitlines() if line.strip()] if queries_file else []
    if queries:
        from kosha.search.embeddings import QUERY_MODEL, embed_texts, record_spend

        embedded, tokens = embed_texts(queries, QUERY_MODEL, "query")
        record_spend(session, user.id, QUERY_MODEL, "embed_demo_queries", tokens, queries=len(queries))
        session.commit()
        vectors = [{"query": q.lower(), "model": QUERY_MODEL, "vector": v} for q, v in {q.lower(): v for q, v in zip(queries, embedded)}.items()]

    bundle = {
        "version": BUNDLE_VERSION, "items": _rows(items + context, ITEM_COLUMNS), "media": _rows(media, MEDIA_COLUMNS),
        "links": _rows(session.scalars(select(ItemLink).where(ItemLink.item_id.in_(every))), LINK_COLUMNS),
        "relations": relations, "embeddings": embeddings,
        "tags": _rows(sorted((tag for tag, _ in names.values() if tag.id in structure), key=lambda t: str(t.path).count(".")), TAG_COLUMNS),
        "item_tags": _rows(item_tags, ITEM_TAG_COLUMNS), "query_vectors": vectors, "queries": queries,
    }
    (out / BUNDLE_FILE).write_text(json.dumps(bundle, ensure_ascii=False))

    per_tag = Counter(names[row.tag_id][1] for row in item_tags)
    per_author = Counter(f"@{i.author_handle}" if i.author_handle else (i.author_name or "(none)") for i in items)
    per_type = Counter(t for i in items for t in (i.content_types or ["(none)"]))
    # No account name here: the summary travels with the bundle.
    lines = [f"Demo bundle, tags: {', '.join(tag_paths)}",
             *([f"Sources: {', '.join(sources)}"] if sources else []),
             *([f"Excluded tags (any role): {', '.join(exclude_tag_paths)}"] if exclude_tag_paths else []),
             f"items={len(items)} (limit {limit}{', spread across the tags' if spread else ''}) context_items={len(context)} "
             f"media_rows={len(media)} links={len(bundle['links'])} embeddings={len(embeddings)} tags={len(bundle['tags'])} "
             f"query_vectors={len(vectors)}",
             f"Media files: {copied} ({copied_bytes} bytes, {copied_bytes / 1e6:.1f} MB)",
             "Left out: notes, stars, read and archive state, Jev answers, captures, raw payloads, lists, views, settings, other tags.",
             "", "Items per tag (an item with two tags counts under both):",
             *(f"  {n:5d}  {path}" for path, n in sorted(per_tag.items(), key=lambda kv: (-kv[1], kv[0]))),
             "", "Items per content type (an item may have several):", *(f"  {n:5d}  {name}" for name, n in per_type.most_common()),
             "", f"Items per author (top {SUMMARY_TOP_AUTHORS} of {len(per_author)}):",
             *(f"  {n:5d}  {author}" for author, n in per_author.most_common(SUMMARY_TOP_AUTHORS))]
    summary = "\n".join(lines)
    (out / SUMMARY_FILE).write_text(summary + "\n")
    return summary


def load_bundle(session: Session, path: Path, password_hash: str | None = None) -> dict:
    """Create the demo user (KOSHA_DEMO_EMAIL / _PASSWORD) and load the bundle
    into an empty database. Does not commit: the caller does, so `reset`
    replaces everything in one transaction."""
    from kosha.auth import hash_password
    from kosha.search.index import reindex

    env = get_settings()
    if not env.kosha_demo or not env.kosha_demo_email or not env.kosha_demo_password:
        raise BundleError("Set KOSHA_DEMO=true, KOSHA_DEMO_EMAIL and KOSHA_DEMO_PASSWORD first: this command is for a demo instance only")
    if session.scalar(select(func.count()).select_from(User)):
        raise BundleError("This database already has users. demo-load needs an empty database; demo-reset replaces a demo's data")
    bundle = json.loads((path / BUNDLE_FILE).read_text())
    if bundle.get("version") != BUNDLE_VERSION:
        raise BundleError(f"Bundle version {bundle.get('version')} is not {BUNDLE_VERSION}; export it again")
    user = User(email=env.kosha_demo_email.strip().lower(), password_hash=password_hash or hash_password(env.kosha_demo_password),
                name="Demo", role="user", onboarding_completed=True)
    session.add(user)
    session.flush()
    for row in bundle["items"]:
        row.update(user_id=user.id, **{c: datetime.fromisoformat(row[c]) for c in DATETIME_COLUMNS if row[c]})
    for row in bundle["media"]:  # the files go into their own folder of the media store
        for column in ("local_path", "local_preview_path"):
            row[column] = f"{DEMO_MEDIA_SUBDIR}/{row[column]}" if row[column] else None
    for row in bundle["tags"]:
        row["user_id"] = user.id
    for model, rows in ((Item, bundle["items"]), (Tag, bundle["tags"]), (ItemMedia, bundle["media"]), (ItemLink, bundle["links"]),
                        (ItemRelation, bundle["relations"]), (ItemEmbedding, bundle["embeddings"]), (ItemTag, bundle["item_tags"]),
                        (DemoQueryVector, bundle["query_vectors"])):
        if rows:
            session.execute(insert(model), rows)
    for table in SERIAL_TABLES:
        session.execute(text(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), (SELECT coalesce(max(id), 1) FROM {table}))"))
    for sequence, table, column in SEQUENCES:
        session.execute(text(f"SELECT setval('{sequence}', (SELECT coalesce(max({column}), 1) FROM {table}))"))
    reindex(session)
    store, files = storage.get_storage(session), path / BUNDLE_MEDIA_DIR
    for file in (f for f in files.rglob("*") if f.is_file()):
        store.put_file(f"{DEMO_MEDIA_SUBDIR}/{file.relative_to(files).as_posix()}", file)
    return {name: len(bundle[name]) for name in ("items", "tags", "media", "embeddings", "query_vectors")}


def reset(session: Session, path: Path) -> dict:
    """Put the demo back to the bundle: empty every table and load again, in
    one transaction, so a visitor sees the old state or the new one. The demo
    user's password hash is kept, so open sessions go on."""
    if not get_settings().kosha_demo:
        raise BundleError("KOSHA_DEMO is not true: demo-reset empties the whole database and is for a demo instance only")
    from kosha.auth import verify_password

    env = get_settings()
    kept = session.scalar(select(User.password_hash).where(User.email == env.kosha_demo_email.strip().lower()))
    if kept and not verify_password(kept, env.kosha_demo_password):
        kept = None  # the published password was changed: hash the new one
    session.execute(text(f"SET LOCAL lock_timeout = '{RESET_LOCK_TIMEOUT_S}s'"))  # fail, rather than hang behind a stuck transaction
    session.execute(text(f"TRUNCATE {', '.join(t.name for t in Base.metadata.sorted_tables)} RESTART IDENTITY CASCADE"))
    return load_bundle(session, path, password_hash=kept)
