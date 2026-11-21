"""The pipeline for the web app and tests (`POST /api/query`, the same
engine the MCP `query` tool runs), and the search bar's Jev check."""
import secrets
from datetime import UTC, datetime

from constella import Question
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from kosha import jev
from kosha.auth import current_user
from kosha.config import PIPELINE_MAX_CHARS
from kosha.db import get_session
from kosha.items_query import STABLE_ORDER
from kosha.models import Item, ResultSet, User
from kosha.pipeline import run_query
from kosha.pipeline.executor import ServiceUnavailable
from kosha.search import engine
from kosha.search.embeddings import EmbeddingError
from kosha.search.query import QueryError, compile_filter, parse

router = APIRouter(tags=["pipeline"])


class QueryIn(BaseModel):
    q: str = Field(max_length=PIPELINE_MAX_CHARS, description='A pipeline, e.g. `where from:simonw | grep ~sqlite | head 20 | show id summary:80`; "help" for the grammar')
    confirm: str | None = Field(None, description="The confirm token a preview returned")
    run: int | None = Field(None, description="A label run id: tags are read and written in that run's namespace")


class JevCheckIn(BaseModel):
    q: str = ""
    mode: str | None = Field(None, description=f"One of {engine.MODES}; as in GET /api/items")
    question: str = Field(min_length=3)
    threshold: float = Field(jev.DEFAULT_THRESHOLD, ge=0, le=1)
    dry_run: bool = False


def _query_error(exc: QueryError) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, exc.as_dict())


@router.post("/query")
def query(body: QueryIn, user: User = Depends(current_user), session: Session = Depends(get_session)) -> dict:
    """Run a pipeline. `text` is the compact response an agent reads; the
    other fields carry the same facts (`n`, per-stage `in`/`out`/`usd`,
    `rows` from `show`, `confirm` for a write preview, `batch_id` to undo).
    An error is a 422 with `{detail: {message, position}}`."""
    try:
        return run_query(session, user.id, body.q, body.confirm, body.run)
    except ServiceUnavailable as exc:  # e.g. a `jev` stage on a server without a TypeSafe key
        session.rollback()
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, exc.as_dict()) from None
    except QueryError as exc:
        session.rollback()
        raise _query_error(exc) from None


@router.post("/jev/check")
def jev_check(body: JevCheckIn, user: User = Depends(current_user), session: Session = Depends(get_session)) -> dict:
    """Ask Jev one yes/no question about every result of a search. With
    `dry_run` nothing is spent: `{estimate_usd, n}`. Otherwise the items at or
    above the threshold are stored, best first, as a snapshot to page through
    with `GET /api/items?snapshot_id=…` (each row's `score` is Jev's p)."""
    if body.mode is not None and body.mode not in engine.MODES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"mode must be one of {engine.MODES}")
    try:
        search = parse(body.q)
        if (search.rank_text or search.similar) and body.mode != "filter":
            ids, _, _ = engine.rank(session, user.id, search, body.mode or "hybrid", [])
        else:
            ids = list(session.scalars(select(Item.id).where(*compile_filter(session, user.id, search)).order_by(*STABLE_ORDER)))
    except QueryError as exc:
        raise _query_error(exc) from None
    except EmbeddingError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"Semantic search is unavailable: {exc}") from None
    texts = jev.item_texts(session, user.id, ids)
    question = Question(body.question.strip())
    pairs = [(question, i, texts[i]) for i in ids if i in texts]
    try:
        estimate = jev.estimate(session, user.id, pairs)
        if body.dry_run:
            return {"estimate_usd": round(estimate.usd, 6), "n": len(pairs), "cached": estimate.n - estimate.to_ask}
        answers, spend = jev.ask(session, user.id, pairs, "jev_check", estimate_usd=estimate.usd)
    except jev.BudgetError as exc:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, str(exc)) from None
    except jev.cj.JevError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"Jev is unavailable: {exc}") from None
    kept = sorted(((a.value, i) for (_, i, _), a in zip(pairs, answers) if a.value >= body.threshold), reverse=True)
    snapshot = ResultSet(user_id=user.id, name=engine.PINNED_PREFIX + secrets.token_hex(8), query=body.question,
                         item_ids=[i for _, i in kept], scores=[round(p, 4) for p, _ in kept],
                         expires_at=datetime.now(UTC) + engine.SNAPSHOT_TTL)
    session.add(snapshot)
    session.commit()
    return {"snapshot_id": snapshot.id, "kept": len(kept), "n": len(pairs), "spent_usd": round(spend.usd, 6),
            "cached": spend.cached, "set": snapshot.name}
