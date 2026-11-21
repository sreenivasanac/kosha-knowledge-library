"""Ask (agentic search) and media text (transcripts and image descriptions)."""
import json
import queue
import threading

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from kosha import ask as ask_module
from kosha import llm, media_text
from kosha.auth import current_user
from kosha.db import SessionLocal, get_session
from kosha.models import User

router = APIRouter(tags=["ai"])


class AskIn(BaseModel):
    question: str = Field(min_length=2, max_length=2000)
    max_steps: int | None = Field(None, ge=1, le=ask_module.MAX_TOOL_CALLS)


class MediaTextIn(BaseModel):
    item_ids: list[int] | None = None
    all: bool = False


def _llm_error(exc: llm.LLMError) -> HTTPException:
    code = status.HTTP_402_PAYMENT_REQUIRED if isinstance(exc, llm.BudgetError) else status.HTTP_503_SERVICE_UNAVAILABLE
    return HTTPException(code, str(exc))


@router.post("/ask")
def ask(body: AskIn, user: User = Depends(current_user), session: Session = Depends(get_session)) -> dict:
    """Answer a question from the library: `{answer, items, steps, spent, took_ms}`.
    `items` are the cited item ids in the order cited; `steps` are the read-only
    pipelines it ran. 402 when the monthly LLM cap is used up."""
    try:
        return ask_module.ask(session, user.id, body.question, body.max_steps)
    except llm.LLMError as exc:
        raise _llm_error(exc) from None


@router.post("/ask/stream")
def ask_stream(body: AskIn, user: User = Depends(current_user)) -> StreamingResponse:
    """The same as `POST /api/ask`, as server-sent events: one `step` event
    per pipeline as it finishes, then `done` with the full answer (or `error`)."""
    events: queue.Queue = queue.Queue()
    user_id = user.id

    def work():
        try:
            with SessionLocal() as session:
                result = ask_module.ask(session, user_id, body.question, body.max_steps, on_step=lambda step: events.put(("step", step)))
            events.put(("done", result))
        except Exception as exc:  # the stream must always end with an event
            events.put(("error", {"message": str(exc)[:300]}))

    def stream():
        threading.Thread(target=work, name="kosha-ask", daemon=True).start()
        while True:
            name, data = events.get()
            yield f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            if name != "step":
                return

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


def _rows(body: MediaTextIn, user: User, session: Session):
    if not body.item_ids and not body.all:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Give item_ids, or all: true")
    return media_text.pending(session, user.id, body.item_ids, body.all)


@router.post("/media-text/estimate")
def media_text_estimate(body: MediaTextIn, user: User = Depends(current_user), session: Session = Depends(get_session)) -> dict:
    """What extracting the text of these items' media would cost:
    `{n_media, minutes, images, estimate_usd}`. Media that already has text is not counted."""
    return media_text.estimate(session, user.id, _rows(body, user, session))


@router.post("/media-text/start", status_code=status.HTTP_202_ACCEPTED)
def media_text_start(body: MediaTextIn, user: User = Depends(current_user), session: Session = Depends(get_session)) -> dict:
    """Queue the media of these items (one item for the `m` key, a selection,
    or `all`) and start the background worker. Answers with the estimate and
    `queued`; follow progress with `GET /api/media-text/status`."""
    rows = _rows(body, user, session)
    estimate = media_text.estimate(session, user.id, rows)
    try:
        llm.check_cap(session, user.id, estimate["estimate_usd"])
    except llm.BudgetError as exc:
        raise _llm_error(exc) from None
    return {**estimate, **media_text.start(session, user.id, rows)}


@router.get("/media-text/status")
def media_text_status(user: User = Depends(current_user), session: Session = Depends(get_session)) -> dict:
    """`{counts: {none, queued, done, failed}, running, job, ffmpeg}`; `job`
    holds the last run's totals and the reason of each failure by media id."""
    return media_text.status(session, user.id)
