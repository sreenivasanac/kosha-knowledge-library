"""`POST /api/captures`: the one way browser captures enter Kosha
(docs/CAPTURE_CONTRACT.md)."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from kosha.auth import current_user
from kosha.db import get_session
from kosha.ingest import ingest_envelope
from kosha.models import User
from kosha.parsers import UnknownCapture
from kosha.schemas import CaptureIn, CaptureOut

router = APIRouter(tags=["captures"])


@router.post("/captures", response_model=CaptureOut)
def post_capture(body: CaptureIn, user: User = Depends(current_user), session: Session = Depends(get_session)):
    try:
        capture = ingest_envelope(session, user.id, body.model_dump())
    except UnknownCapture as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    session.commit()  # the envelope is kept even when parsing failed
    if capture.parse_error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"Capture {capture.id} stored but not parsed: {capture.parse_error}")
    return {"capture_id": capture.id, "items_new": capture.items_new,
            "items_updated": capture.items_updated, "items_unchanged": capture.items_unchanged}
