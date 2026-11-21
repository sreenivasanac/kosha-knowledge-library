"""Import bookmarks from a file and export the library (kosha/importers/)."""
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from kosha import importers
from kosha.auth import current_user
from kosha.config import APP_NAME
from kosha.db import SessionLocal, get_session
from kosha.importers import export as exporter
from kosha.models import User
from kosha.search.query import QueryError, compile_filter, parse

router = APIRouter(tags=["portability"])


@router.post("/import")
def import_file(
    file: UploadFile = File(...),
    format: str | None = Query(None, description=f"One of {', '.join(importers.FORMATS)}; found from the content when left out"),
    dry_run: bool = Query(False),
    format_field: str | None = Form(None, alias="format"),
    dry_run_field: bool = Form(False, alias="dry_run"),
    user: User = Depends(current_user), session: Session = Depends(get_session),
) -> dict:
    """Import a bookmarks file (multipart `file`): Netscape bookmarks HTML
    (browsers, Raindrop, Pocket), Raindrop CSV, or any CSV with a `url`
    column. `format` and `dry_run` may be query parameters or form fields.

    Answers `{format, created, duplicates, skipped, errors}`; `errors` explains
    at most 20 skipped rows. With `dry_run=true` nothing is written and the
    first 10 parsed rows come back as `rows`. The URLs are not fetched."""
    data = file.file.read(importers.IMPORT_MAX_BYTES + 1)
    if len(data) > importers.IMPORT_MAX_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"The file is over {importers.IMPORT_MAX_BYTES // 2**20} MB")
    dry_run = dry_run or dry_run_field
    try:
        found, rows = importers.parse(data, format or format_field)
    except importers.ImportError_ as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    result = importers.run_import(session, user.id, rows, dry_run=dry_run)
    return {"format": found, **result, **({"rows": importers.preview(rows)} if dry_run else {})}


@router.get("/export")
def export_library(format: Literal["csv", "html", "json"] = "json", q: str = "",
                   user: User = Depends(current_user), session: Session = Depends(get_session)) -> StreamingResponse:
    """Download the saved items `q` matches (the search bar's qualifiers;
    everything saved and not archived without it). `csv` has Raindrop's
    columns, `html` is a Netscape bookmarks file with tag paths in `TAGS`,
    `json` has every field the API shows, without raw payloads or embeddings."""
    try:
        compile_filter(session, user.id, parse(q))  # a query that does not parse is refused before the download starts
    except QueryError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, exc.as_dict()) from None
    user_id = user.id

    def body():
        with SessionLocal() as own:  # the request's session is closed before the stream ends
            yield from exporter.stream(own, user_id, format, q)

    name = f"{APP_NAME.lower()}-export-{datetime.now(UTC):%Y-%m-%d}.{format}"
    return StreamingResponse(body(), media_type=exporter.MEDIA_TYPES[format],
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})
