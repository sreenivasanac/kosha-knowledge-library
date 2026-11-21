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


MEDIA_TEXT_CONFIRM_ABOVE_USD = 0.50  # a start estimated above this (and every `all`) needs `confirm_usd`


class MediaTextIn(BaseModel):
    item_ids: list[int] | None = None
    all: bool = False
    confirm_usd: float | None = Field(None, description="The estimate the user agreed to; needed for `all` and above "
                                                        f"${MEDIA_TEXT_CONFIRM_ABOVE_USD:.2f}, and must cover the current estimate")


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
    per pipeline as it finishes, then `done` with the full answer, or `error`
    with `{status, message}`: the status `POST /api/ask` would have answered
    with (402 when the monthly LLM cap is used up, 503 when the model cannot be reached)."""
    events: queue.Queue = queue.Queue()
    user_id = user.id

    def work():
        try:
            with SessionLocal() as session:
                result = ask_module.ask(session, user_id, body.question, body.max_steps, on_step=lambda step: events.put(("step", step)))
            events.put(("done", result))
        except llm.LLMError as exc:
            error = _llm_error(exc)
            events.put(("error", {"status": error.status_code, "message": str(error.detail)[:300]}))
        except Exception as exc:  # the stream must always end with an event
            events.put(("error", {"status": status.HTTP_500_INTERNAL_SERVER_ERROR, "message": str(exc)[:300]}))

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
    `{n_media, minutes, images, unknown_duration, estimate_usd}`. Media that already has text is not
    counted; `unknown_duration` recordings have no length yet and are not in the estimate."""
    return media_text.estimate(session, user.id, _rows(body, user, session))


@router.post("/media-text/start", status_code=status.HTTP_202_ACCEPTED)
def media_text_start(body: MediaTextIn, user: User = Depends(current_user), session: Session = Depends(get_session)) -> dict:
    """Queue the media of these items (one item for the `m` key, a selection,
    or `all`) and start the background worker. Answers with the estimate and
    `queued`; follow progress with `GET /api/media-text/status`.

    With `all`, or an estimate over MEDIA_TEXT_CONFIRM_ABOVE_USD, the request
    must carry `confirm_usd` at or above the current estimate. Otherwise 409
    `{detail: {message, n_media, minutes, images, unknown_duration, estimate_usd}}`
    and nothing is queued: show the estimate, then send it back as `confirm_usd`."""
    rows = _rows(body, user, session)
    estimate = media_text.estimate(session, user.id, rows)
    usd = estimate["estimate_usd"]
    if (body.all or usd > MEDIA_TEXT_CONFIRM_ABOVE_USD) and (body.confirm_usd is None or body.confirm_usd < usd):
        raise HTTPException(status.HTTP_409_CONFLICT, {
            "message": f"This would cost about ${usd:.2f}. Send confirm_usd: {usd} to start.", **estimate})
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
