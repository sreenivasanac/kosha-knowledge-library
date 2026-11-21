"""Kosha's MCP server (SEARCH_DESIGN.md H.2): six tools over streamable HTTP
at /mcp, in the same process as the API and with the same bearer tokens.

The tool descriptions are the agent's only manual, so `query` carries the
whole pipeline grammar. Tools return plain text sized for a model to read.
"""
import contextvars
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
from kosha.config import APP_NAME, DEFAULT_NAMESPACE, MCP_PATH
from kosha.db import SessionLocal
from kosha.models import Item, LabelRun, SyncState, User
from kosha.pipeline import GRAMMAR, run_query, writes
from kosha.schemas import TagMerge
from kosha.search.embeddings import DOC_MODEL, embed_items
from kosha.search.index import reindex
from kosha.search.query import QueryError
from kosha.summaries import summarize
from kosha.sync.x_api import sync_bookmarks

TREE_MAX_LINES = 400  # about 10 tokens a tag
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
        with SessionLocal() as session:
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


def _tag_op(session, user: User, namespace: str, op: dict) -> str:
    """One change to the tree. Paths are full paths of names from the root ("AI/RAG")."""
    kind, path = str(op.get("op", "")).lower(), str(op.get("path", "")).strip()
    if kind == "create":
        if writes.find_tag(session, user.id, namespace, path) is not None:
            return f"exists {path}"
        tag = writes.create_tag_path(session, user.id, namespace, path)
        tag.description = op.get("description")
        session.commit()
        return f"created {path} (proposed)"
    tag = writes.find_tag(session, user.id, namespace, path)
    if tag is None:
        raise ValueError(f'No tag at "{path}" in namespace {namespace}')
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
        into = writes.find_tag(session, user.id, namespace, str(op.get("into", "")))
        if into is None:
            raise ValueError(f'No tag at "{op.get("into")}" to merge into')
        tags_api.merge(tag.id, TagMerge(into_id=into.id), user=user, session=session)
        return f"merged {path} into {op['into']}"
    elif kind in ("approve", "reject"):
        tag.status = "approved" if kind == "approve" else "rejected"
    elif kind == "delete":
        tags_api.delete_tag(tag.id, recursive=bool(op.get("recursive")), user=user, session=session)
        return f"deleted {path}"
    else:
        raise ValueError(f'Unknown op "{kind}". Use create, rename, describe, move, merge, approve, reject or delete')
    tags_api._commit(session)
    return f"{kind} {path}: ok"


def _namespace(session, user: User, run: int | None) -> str:
    return label_runs.get_run(session, user.id, run).namespace if run is not None else DEFAULT_NAMESPACE


@mcp.tool(structured_output=False, description="Read or change the tag tree. With no `operations`: the tree with item counts, status and each tag's "
                      "one-sentence definition. `operations` is a list applied in order, then the new tree is returned:\n"
                      '  {"op":"create","path":"AI/RAG","description":"Retrieval-augmented generation: ..."}  (new tags are proposed)\n'
                      '  {"op":"rename","path":"AI/RAG","name":"Retrieval"}   {"op":"describe","path":"AI/RAG","description":"..."}\n'
                      '  {"op":"move","path":"AI/RAG","parent":"ML"}  (parent "" = top level)   {"op":"merge","path":"AI/Rag2","into":"AI/RAG"}\n'
                      '  {"op":"approve","path":"AI/RAG"}   {"op":"reject","path":"AI/RAG"}   {"op":"delete","path":"AI/RAG","recursive":false}\n'
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
            except (ValueError, HTTPException, KeyError) as exc:
                session.rollback()
                done.append(f"FAILED {op.get('op')} {op.get('path')}: {getattr(exc, 'detail', None) or exc} (later operations not run)")
                break
        return "\n".join([*done, _tree(session, user, namespace)])

    return await _call(work)


# --- edit_item --------------------------------------------------------------
@mcp.tool(structured_output=False, description="Change one item (id from `query ... | show id`). Any of: `note` (your note; \"\" clears it), `summary` "
                      "(replaces the stored summary; kept as agent-written), `starred`, `read`, `archived` (true/false), "
                      "`primary_tag` / `secondary_tag` (a tag path: attaches it with that role). For many items use `query` "
                      "with a write stage. To read an item: query `where id:<id> | show author date body:2000 url tags`.")
async def edit_item(id: int, note: str | None = None, summary: str | None = None, starred: bool | None = None,
                    read: bool | None = None, archived: bool | None = None, primary_tag: str | None = None,
                    secondary_tag: str | None = None) -> str:
    def work(session, user):
        item = session.get(Item, id)
        if item is None or item.user_id != user.id:
            raise ValueError(f"No item {id}")
        now, changed = datetime.now(UTC), []
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
                counts, _ = writes.apply_tags(session, user.id, DEFAULT_NAMESPACE, [(item.id, tag.id, role, None)])
                changed.append(f"{role}_tag" + (" (skipped: you removed this tag or set it by hand)" if counts["removed_by_you"] or counts["by_hand"] else ""))
        if "note" in changed or "summary" in changed:
            reindex(session, [item.id])  # both are searchable
        session.commit()
        return f"item {id}: changed {', '.join(changed)}" if changed else f"item {id}: nothing to change"

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
                      "  start    needs `name`, `strategy`; optional `budget_usd`. Returns the run id to pass as `run` to query and tags\n"
                      "  status   `run`: coverage (share of saved items with a primary tag), untagged, tags, dollars spent, budget\n"
                      "  close    `run`: finish it and record final coverage and spend\n"
                      "  compare  `runs` [a, b, ...]: the same measures side by side\n"
                      "  promote  `run`: copy its tree into the real one as proposed tags. Returns a preview and a confirm token; "
                      "call again with `confirm`\n"
                      "  undo     `batch`: reverse one write (the batch id a write returned)\n"
                      "  list     every run")
async def runs(action: str, run: int | None = None, name: str | None = None, strategy: str | None = None,
               budget_usd: float | None = None, runs: list[int] | None = None, batch: int | None = None,
               confirm: str | None = None) -> str:
    def work(session, user):
        if action == "start":
            if not name or not strategy:
                raise ValueError("start needs name and strategy")
            started = label_runs.start(session, user.id, name, strategy, budget_usd)
            return f"run={started.id} namespace={started.namespace} budget_usd={budget_usd}. Pass run={started.id} to query and tags."
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
            with SessionLocal() as session:
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

