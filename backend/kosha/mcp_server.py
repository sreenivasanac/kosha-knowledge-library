"""Kosha's MCP server (SEARCH_DESIGN.md H.2): six tools over streamable HTTP
at /mcp, in the same process as the API and with the same bearer tokens.

The tool descriptions are the agent's only manual, so `query` carries the
whole pipeline grammar. Tools return plain text sized for a model to read.
"""
import contextvars
import json
import threading
from datetime import UTC, datetime

import anyio
from fastapi import HTTPException
from mcp.server.mcpserver import MCPServer
from mcp.server.streamable_http_manager import StreamableHTTPASGIApp
from sqlalchemy import select, text
from starlette.responses import JSONResponse

from kosha import runs as label_runs
from kosha.add_item import add_item as add_one
from kosha.api import tags as tags_api
from kosha.api.items import undo_batch
from kosha.auth import bearer_user
from kosha.config import APP_NAME, DEFAULT_NAMESPACE, MCP_PATH, PIPELINE_MAX_CHARS
from kosha.db import RequestSession, SessionLocal
from kosha.models import Item, ItemTag, LabelRun, SyncState, User
from kosha.pipeline import GRAMMAR, run_query, writes
from kosha.search.embeddings import DOC_MODEL, embed_items
from kosha.search.index import reindex
from kosha.search.query import QueryError
from kosha.summaries import summarize
from kosha.sync.x_api import sync_bookmarks

TREE_MAX_LINES = 400  # about 10 tokens a tag
TAG_COLUMNS = ("id", "parent_id", "path", "name", "slug", "description", "status", "origin", "namespace", "color", "sort")
GATED_TAG_OPS = ("rename", "move", "merge", "delete")  # need a confirm token when they touch the owner's work
PREVIEW_PATHS = 10  # affected tag paths listed in a tags preview
METHOD_AGENT = "agent"  # items.summary_method for a summary an agent wrote; `kosha summarize` leaves it alone

mcp = MCPServer(APP_NAME.lower(), log_level="WARNING", instructions=(  # WARNING: the SDK installs a root log handler at this level
    "Kosha is a library of saved posts and pages. Use `query` for everything that reads, judges or bulk-writes items: "
    "it runs a pipeline on the server and returns counts and only the fields you ask for. Item text stays on the server; "
    "read counts, terms and short samples rather than whole items."))

_user_id: contextvars.ContextVar[int] = contextvars.ContextVar("kosha_mcp_user")


def _call(fn):
    """Run a tool's database work in a worker thread with its own session, as
    the user the bearer token belongs to. Expected errors come back as text
    the agent can act on."""
    user_id = _user_id.get()

    def work() -> str:
        with RequestSession() as session:
            try:
                return fn(session, session.get(User, user_id))
            except QueryError as exc:
                session.rollback()
                return f"ERROR at character {exc.position}: {exc.message}"
            except HTTPException as exc:
                session.rollback()
                return f"ERROR: {exc.detail}"
            except (ValueError, label_runs.RunError) as exc:
                session.rollback()
                return f"ERROR: {exc}"

    return anyio.to_thread.run_sync(work)


def _fmt(values: dict) -> str:
    return " ".join(f"{k}={v}" for k, v in values.items() if v is not None)


# --- query ------------------------------------------------------------------
@mcp.tool(structured_output=False, description="Run a pipeline over the library: filter, search, judge with Jev, count, show, and bulk-write with "
                      "preview. One call does a whole chain on the server.\n\n" + GRAMMAR +
                      "\nReplies start with `n=<items> spent=$<this call>` and one line of `stage in→out` per stage. "
                      "A write over 25 items returns PREVIEW and a confirm token: repeat the same pipeline with `confirm`. "
                      f"A pipeline is at most {PIPELINE_MAX_CHARS} characters. "
                      "`run` is a label-run id (see `runs`): tags are then read and written in that run's trial namespace. "
                      "Errors give the character position in your pipeline.")
async def query(pipeline: str, confirm: str | None = None, run: int | None = None) -> str:
    return await _call(lambda session, user: run_query(session, user.id, pipeline, confirm, run)["text"])


# --- tags -------------------------------------------------------------------
def _tree(session, user: User, namespace: str) -> str:
    nodes = tags_api.tag_tree(namespace=namespace, status_=None, user=user, session=session)
    lines: list[str] = []

    def walk(node: dict, depth: int) -> None:
        mark = "" if node["status"] == "approved" else f" [{node['status']}]"
        about = f" — {node['description']}" if node["description"] else ""
        lines.append(f"{'  ' * depth}{node['name']}  {node['count']}{mark}{about}")
        for child in node["children"]:
            walk(child, depth + 1)

    for root in nodes:
        walk(root, 0)
    if not lines:
        return f"No tags in namespace {namespace}."
    more = f"\n... {len(lines) - TREE_MAX_LINES} more tags" if len(lines) > TREE_MAX_LINES else ""
    return f"tags in {namespace} (name  items incl. children  [status] — definition):\n" + "\n".join(lines[:TREE_MAX_LINES]) + more


def _undo_hint(batch_id: int) -> str:
    return f"batch={batch_id} (undo: runs undo {batch_id})"


def _tag_op(session, user: User, namespace: str, op: dict) -> str:
    """One change to the tree. Paths are full paths of names from the root ("AI/RAG").

    Safety rules for an agent (the owner's web app is not bound by them):
    - `approve` and `reject` do not exist here: the owner decides those.
    - rename, move, merge and delete apply at once only when every tag in the
      subtree is the agent's own (origin agent, or still proposed) and carries
      no row attached by hand and no removal marker. Otherwise the reply is a
      PREVIEW with counts, the affected paths and a confirm token bound to
      this operation and to exactly those tags and rows.
    - every change first saves what it replaces as an undo batch.
    - a merge never brings back a tag the user removed from an item."""
    kind, path = str(op.get("op", "")).lower(), str(op.get("path", "")).strip()
    if kind == "create":
        if writes.find_tag(session, user.id, namespace, path) is not None:
            return f"exists {path}"
        had = set(writes.tag_paths(session, user.id, namespace))
        tag = writes.create_tag_path(session, user.id, namespace, path)
        tag.description = op.get("description")
        batch_id = writes._save_batch(session, user.id, {"created_tags": sorted(set(writes.tag_paths(session, user.id, namespace)) - had)})
        session.commit()
        return f"created {path} (proposed) {_undo_hint(batch_id)}"
    if kind in ("approve", "reject"):
        raise ValueError(f"{kind} is the owner's decision: they approve or reject tags in the web app")
    if kind not in (*GATED_TAG_OPS, "describe"):
        raise ValueError(f'Unknown op "{kind}". Use create, rename, describe, move, merge or delete')
    tag = writes.find_tag(session, user.id, namespace, path)
    if tag is None:
        raise ValueError(f'No tag at "{path}" in namespace {namespace}')
    into = None
    if kind == "merge":
        into = writes.find_tag(session, user.id, namespace, str(op.get("into", "")))
        if into is None:
            raise ValueError(f'No tag at "{op.get("into")}" to merge into')
    # What the operation touches: the tag alone for describe, else its whole subtree.
    tree = sorted(((t, p) for t, p in writes.tag_paths(session, user.id, namespace).values()
                   if t.id == tag.id or (kind != "describe" and tags_api.is_within(str(t.path), str(tag.path)))), key=lambda pair: pair[1])
    if kind == "delete" and len(tree) > 1 and not op.get("recursive"):
        raise ValueError("Tag has child tags; pass recursive: true to delete them too")
    ids = [t.id for t, _ in tree]
    rows = list(session.scalars(select(ItemTag).where(ItemTag.tag_id.in_(ids)).order_by(ItemTag.item_id, ItemTag.tag_id)))
    owners = [p for t, p in tree if t.origin != writes.AGENT and t.status != "proposed"]
    by_hand = sum(r.attached_by != writes.AGENT and not r.removed_by_user for r in rows)
    removed = sum(r.removed_by_user for r in rows)
    if kind in GATED_TAG_OPS and (owners or by_hand or removed):
        digest = writes.digest_of("tags", namespace, json.dumps({k: v for k, v in op.items() if k != "confirm"}, sort_keys=True, default=str),
                                  ids, [(r.item_id, r.tag_id, r.attached_by, r.removed_by_user) for r in rows])
        if not writes.confirm_ok(user.id, digest, op.get("confirm")):
            listed = ", ".join(p for _, p in tree[:PREVIEW_PATHS]) + (f", ... {len(tree) - PREVIEW_PATHS} more" if len(tree) > PREVIEW_PATHS else "")
            return (f"PREVIEW {kind} {path}: nothing changed, later operations not run. It touches {len(tree)} tag(s), "
                    f"{len(owners)} of them made or approved by the owner, and {len(rows)} tag row(s) on {len({r.item_id for r in rows})} item(s): "
                    f"by_hand={by_hand} removed_by_owner={removed}.\n  tags: {listed}\n"
                    f'To apply, send this operation again with "confirm": "{writes.confirm_token(user.id, digest)}" '
                    f"(valid {writes.CONFIRM_TTL_S // 60} min). It can be undone afterwards.")
    # The undo batch: the tags as they are, and the item rows the operation changes or drops.
    if kind == "merge":
        mine = [r for r in rows if r.tag_id == tag.id]
        kept, tag_ids = mine + list(session.scalars(select(ItemTag).where(
            ItemTag.tag_id == into.id, ItemTag.item_id.in_({r.item_id for r in mine})))), [tag.id, into.id]
    else:
        kept, tag_ids = (rows, ids) if kind == "delete" else ([], [])
    batch_id = writes._save_batch(session, user.id, {
        "tags": [{c: str(getattr(t, c)) if c == "path" else getattr(t, c) for c in TAG_COLUMNS} for t, _ in tree],
        "items": [{"id": i} for i in sorted({r.item_id for r in kept})], "tag_ids": tag_ids,
        "item_tags": [{c: getattr(r, c) for c in writes.ITEM_TAG_COLUMNS} for r in kept]})
    if kind == "rename":
        tag.name, tag.slug = str(op["name"]).strip(), tags_api.slugify(str(op["name"]))
    elif kind == "describe":
        tag.description = op.get("description")
    elif kind == "move":
        parent = str(op.get("parent") or "").strip()
        target = writes.find_tag(session, user.id, namespace, parent) if parent else None
        if parent and target is None:
            raise ValueError(f'No tag at "{parent}" to move under')
        tags_api.move_tag(session, tag, target)
    elif kind == "merge":
        tags_api.merge_tags(session, user, tag, into, restore_removed=False)
        return f"merged {path} into {op['into']} {_undo_hint(batch_id)}"
    else:
        tags_api.delete_tag(tag.id, recursive=bool(op.get("recursive")), user=user, session=session)
        return f"deleted {path} {_undo_hint(batch_id)}"
    tags_api._commit(session)
    return f"{kind} {path}: ok {_undo_hint(batch_id)}"


def _namespace(session, user: User, run: int | None) -> str:
    return label_runs.get_run(session, user.id, run).namespace if run is not None else DEFAULT_NAMESPACE


@mcp.tool(structured_output=False, description="Read or change the tag tree. With no `operations`: the tree with item counts, status and each tag's "
                      "one-sentence definition. `operations` is a list applied in order, then the new tree is returned:\n"
                      '  {"op":"create","path":"AI/RAG","description":"Retrieval-augmented generation: ..."}  (new tags are proposed)\n'
                      '  {"op":"rename","path":"AI/RAG","name":"Retrieval"}   {"op":"describe","path":"AI/RAG","description":"..."}\n'
                      '  {"op":"move","path":"AI/RAG","parent":"ML"}  (parent "" = top level)   {"op":"merge","path":"AI/Rag2","into":"AI/RAG"}\n'
                      '  {"op":"delete","path":"AI/RAG","recursive":false}\n'
                      "Approving and rejecting tags is the owner's, in the web app. Every change returns `batch=<id>`: "
                      "`runs` undo reverses it. rename, move, merge and delete apply at once on your own tags (agent-made or "
                      "still proposed, with no tag row attached by hand); on anything the owner made, approved or attached "
                      'they return PREVIEW with counts and a token: send the same operation again with "confirm":"c_...". '
                      "A merge never puts back a tag the owner removed from an item.\n"
                      "Paths are full paths of names from the root. Write the definition so a yes/no question can be answered "
                      "from it: it is what `score_labels` asks Jev. Pass `run` to work on a label run's trial tree. "
                      "Attach tags to items with `query` (... | tag \"AI/RAG\").")
async def tags(operations: list[dict] | None = None, run: int | None = None) -> str:
    def work(session, user):
        namespace = _namespace(session, user, run)
        done = []
        for op in operations or []:
            try:
                done.append(_tag_op(session, user, namespace, op))
                if done[-1].startswith("PREVIEW"):
                    session.rollback()
                    break
            except (ValueError, HTTPException, KeyError) as exc:
                session.rollback()
                done.append(f"FAILED {op.get('op')} {op.get('path')}: {getattr(exc, 'detail', None) or exc} (later operations not run)")
                break
        return "\n".join([*done, _tree(session, user, namespace)])

    return await _call(work)


# --- edit_item --------------------------------------------------------------
@mcp.tool(structured_output=False, description="Change one item (id from `query ... | show id`). Any of: `note` (your note; \"\" clears it), `summary` "
                      "(replaces the stored summary; kept as agent-written), `starred`, `read`, `archived` (true/false), "
                      "`primary_tag` / `secondary_tag` (a tag path: attaches it with that role). The reply ends with `batch=<id>`: "
                      "`runs` undo with it puts back the note, summary, state and tags this call replaced. For many items use `query` "
                      "with a write stage. To read an item: query `where id:<id> | show author date body:2000 url tags`.")
async def edit_item(id: int, note: str | None = None, summary: str | None = None, starred: bool | None = None,
                    read: bool | None = None, archived: bool | None = None, primary_tag: str | None = None,
                    secondary_tag: str | None = None) -> str:
    def work(session, user):
        item = session.get(Item, id)
        if item is None or item.user_id != user.id:
            raise ValueError(f"No item {id}")
        now, changed = datetime.now(UTC), []
        # What this call may replace, kept as one undo batch: the item's state, note and summary, and its tag rows.
        tag_rows = list(session.scalars(select(ItemTag).where(ItemTag.item_id == item.id, ItemTag.namespace == DEFAULT_NAMESPACE)))
        before = {"items": [{"id": item.id, "starred": item.starred, "note": item.note,
                             "read_at": item.read_at.isoformat() if item.read_at else None,
                             "archived_at": item.archived_at.isoformat() if item.archived_at else None,
                             "summary": item.summary, "summary_method": item.summary_method, "summary_status": item.summary_status}],
                  "tag_ids": [r.tag_id for r in tag_rows], "item_tags": [{c: getattr(r, c) for c in writes.ITEM_TAG_COLUMNS} for r in tag_rows]}
        if note is not None:
            item.note = note or None
            changed.append("note")
        if summary is not None:
            item.summary, item.summary_method, item.summary_status = summary.strip() or None, METHOD_AGENT, "done"
            changed.append("summary")
        if starred is not None:
            item.starred = starred
            changed.append("starred")
        if read is not None:
            item.read_at = (item.read_at or now) if read else None
            changed.append("read")
        if archived is not None:
            item.archived_at = (item.archived_at or now) if archived else None
            changed.append("archived")
        for role, path in (("primary", primary_tag), ("secondary", secondary_tag)):
            if path:
                tag = writes.find_tag(session, user.id, DEFAULT_NAMESPACE, path)
                if tag is None:
                    raise ValueError(f'No tag at "{path}". Create it with the tags tool first.')
                before["tag_ids"] = sorted({*before["tag_ids"], tag.id})
                counts, _ = writes.apply_tags(session, user.id, DEFAULT_NAMESPACE, [(item.id, tag.id, role, None)])
                changed.append(f"{role}_tag" + (" (skipped: you removed this tag or set it by hand)" if counts["removed_by_you"] or counts["by_hand"] else ""))
        if "note" in changed or "summary" in changed:
            reindex(session, [item.id])  # both are searchable
        if not changed:
            return f"item {id}: nothing to change"
        batch_id = writes._save_batch(session, user.id, before)
        session.commit()
        return f"item {id}: changed {', '.join(changed)} {_undo_hint(batch_id)}"

    return await _call(work)


# --- add_item ---------------------------------------------------------------
@mcp.tool(structured_output=False, description="Save a new item: a `url` (Kosha reads the page's title and description itself; https pages only) "
                      "or a piece of `text`, with an optional `title`, `note` and `tags` (tag paths; attached as agent tags, "
                      "new ones are created as proposed). The same URL twice is one item. Returns the item id. "
                      "The item is searchable by keyword at once and by meaning after `jobs` embed.")
async def add_item(url: str | None = None, text: str | None = None, title: str | None = None, note: str | None = None,
                   tags: list[str] | None = None) -> str:
    def work(session, user):
        item, is_new = add_one(session, user.id, url=url, text=text, title=title, note=note)
        for path in tags or []:
            tag = writes.find_tag(session, user.id, DEFAULT_NAMESPACE, path) or writes.create_tag_path(session, user.id, DEFAULT_NAMESPACE, path)
            writes.apply_tags(session, user.id, DEFAULT_NAMESPACE, [(item.id, tag.id, None, None)])
        session.commit()
        return f"{'added' if is_new else 'already held'} item {item.id}: {(item.title or item.body or item.url or '')[:80]}"

    return await _call(work)


# --- jobs -------------------------------------------------------------------
_embed_job: dict = {"running": False, "last": None}


def _embed_in_background(user_id: int) -> None:
    def work():
        try:
            with SessionLocal() as session:
                _embed_job["last"] = _fmt(embed_items(session, user_id))
        except Exception as exc:  # reported by `jobs status`; the next start resumes where this stopped
            _embed_job["last"] = f"failed: {exc}"
        finally:
            _embed_job["running"] = False

    _embed_job["running"] = True
    threading.Thread(target=work, name="kosha-embed", daemon=True).start()


@mcp.tool(structured_output=False, description="Background work. `action`:\n"
                      "  status     counts of saved items, items without a summary or an embedding, items archived and waiting "
                      "to be removed at the source, the last sync per source, and whether an embed job is running\n"
                      "  summarize  write rule-built summaries for new or changed items (free)\n"
                      "  embed      embed new or changed items so `semantic`, `hybrid`, `similar` and `groups` see them "
                      "(Voyage, about $0.02 per 1,000 items; runs in the background, check with status)\n"
                      "  sync       run the official X API bookmark sync once (does nothing unless the user turned it on)")
async def jobs(action: str = "status") -> str:
    def work(session, user):
        if action == "summarize":
            return "summarize: " + _fmt(summarize(session, user.id))
        if action == "embed":
            if _embed_job["running"]:
                return "embed: already running"
            _embed_in_background(user.id)
            return "embed: started; check with action=status"
        if action == "sync":
            return "sync: " + _fmt(sync_bookmarks(session, user.id))
        if action != "status":
            raise ValueError("action is one of: status, summarize, embed, sync")
        row = session.execute(text("""
            SELECT count(*) FILTER (WHERE archived_at IS NULL) AS saved,
              count(*) FILTER (WHERE archived_at IS NULL AND summary IS NULL) AS no_summary,
              count(*) FILTER (WHERE archived_at IS NULL AND NOT EXISTS (
                  SELECT 1 FROM item_embeddings e WHERE e.item_id = items.id AND e.model = :model)) AS no_embedding,
              count(*) FILTER (WHERE archived_at IS NOT NULL) AS removal_pending
            FROM items WHERE user_id = :u AND is_saved"""), {"u": user.id, "model": DOC_MODEL}).one()
        lines = ["items: " + _fmt(row._asdict()),
                 f"embed job: {'running' if _embed_job['running'] else 'idle'}" + (f"; last: {_embed_job['last']}" if _embed_job["last"] else "")]
        for state in session.scalars(select(SyncState).where(SyncState.user_id == user.id)):
            lines.append(f"sync {state.source}: updated={state.updated_at:%Y-%m-%d %H:%M} " + _fmt({k: v for k, v in (state.state or {}).items() if not isinstance(v, (dict, list))}))
        last = session.scalar(text("SELECT max(received_at) FROM captures WHERE user_id = :u"), {"u": user.id})
        lines.append(f"last capture from the extension: {last:%Y-%m-%d %H:%M}" if last else "no captures yet")
        return "\n".join(lines)

    return await _call(work)


# --- runs -------------------------------------------------------------------
@mcp.tool(structured_output=False, description="Label runs: an experiment gets its own trial tag tree (a namespace), so it never touches the real one. "
                      "`action`:\n"
                      "  start    needs `name`, `strategy`; optional `budget_usd` (default: the per-call budget; never over the owner's "
                      "maximum, and the reply says what you got). Returns the run id to pass as `run` to query and tags\n"
                      "  status   `run`: coverage (share of saved items with a primary tag), untagged, tags, dollars spent, budget\n"
                      "  close    `run`: finish it and record final coverage and spend\n"
                      "  compare  `runs` [a, b, ...]: the same measures side by side\n"
                      "  promote  `run`: copy its tree into the real one as proposed tags. Returns a preview and a confirm token; "
                      "call again with `confirm`\n"
                      "  undo     `batch`: reverse one write (the batch id a `query` write, a `tags` change or `edit_item` returned)\n"
                      "  list     every run")
async def runs(action: str, run: int | None = None, name: str | None = None, strategy: str | None = None,
               budget_usd: float | None = None, runs: list[int] | None = None, batch: int | None = None,
               confirm: str | None = None) -> str:
    def work(session, user):
        if action == "start":
            if not name or not strategy:
                raise ValueError("start needs name and strategy")
            started = label_runs.start(session, user.id, name, strategy, budget_usd)
            return (f"run={started.id} namespace={started.namespace} budget_usd={started.params['budget_usd']}. "
                    f"Pass run={started.id} to query and tags.")
        if action == "list":
            rows = session.scalars(select(LabelRun).where(LabelRun.user_id == user.id).order_by(LabelRun.id)).all()
            return "\n".join(f"run={r.id} {(r.params or {}).get('name')} strategy={r.strategy} {r.status} coverage={r.coverage}" for r in rows) or "no runs"
        if action == "compare":
            if not runs or len(runs) < 2:
                raise ValueError("compare needs runs=[a, b]")
            return label_runs.compare(session, user.id, runs)
        if action == "undo":
            if batch is None:
                raise ValueError("undo needs batch")
            undo_batch(session, user.id, batch)
            return f"undone batch {batch}"
        if run is None:
            raise ValueError(f"{action} needs run")
        target = label_runs.get_run(session, user.id, run)
        if action == "status":
            return _fmt(label_runs.status(session, user.id, target))
        if action == "close":
            return "closed: " + _fmt(label_runs.close(session, user.id, target))
        if action == "promote":
            result = label_runs.promote(session, user.id, target, confirm)
            return ("promoted: " if result["applied"] else "PREVIEW (nothing written): ") + _fmt(result)
        raise ValueError("action is one of: start, status, close, compare, promote, undo, list")

    return await _call(work)


# --- HTTP -------------------------------------------------------------------
class McpEndpoint:
    """The ASGI endpoint mounted at /mcp: checks the bearer token against
    `api_tokens`, remembers whose call this is, then hands over to the SDK."""

    def __init__(self):
        # Stateless JSON responses: every call stands alone, so there is no session to resume or expire.
        mcp.streamable_http_app(streamable_http_path=MCP_PATH, json_response=True, stateless_http=True)
        self.session_manager = mcp.session_manager
        self.app = StreamableHTTPASGIApp(self.session_manager)

    async def __call__(self, scope, receive, send):
        header = dict(scope["headers"]).get(b"authorization", b"").decode("latin-1")

        def authenticate() -> int | None:
            with RequestSession() as session:
                user = bearer_user(session, header)
                session.commit()  # last_used_at
                return user.id if user else None

        user_id = await anyio.to_thread.run_sync(authenticate)
        if user_id is None:
            response = JSONResponse({"detail": "Send Authorization: Bearer <KOSHA_API_TOKEN>"}, 401,
                                    headers={"WWW-Authenticate": "Bearer"})
            return await response(scope, receive, send)
        _user_id.set(user_id)
        await self.app(scope, receive, send)

