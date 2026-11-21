"""Search-bar support: autocomplete, the chip list, recent searches, saved views."""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from kosha.auth import current_user
from kosha.db import get_session
from kosha.models import SavedView, User
from kosha.schemas import ParseIn, ParseOut, SavedViewIn, SavedViewOut, SavedViewPatch, SuggestOut
from kosha.search.engine import recent_searches
from kosha.search.query import QueryError, compile_filter, parse, to_string
from kosha.search.suggest import suggest

router = APIRouter(tags=["search"])


def _checked(session: Session, user: User, q: str):
    """Parse and compile (which also resolves tags and sets), or raise 422
    with `{message, position}`."""
    try:
        query = parse(q)
        compile_filter(session, user.id, query)
    except QueryError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, exc.as_dict()) from None
    return query


@router.get("/search/suggest", response_model=SuggestOut)
def search_suggest(q: str = "", cursor: int | None = Query(None, ge=0, description="Caret position; default: end of q"),
                   user: User = Depends(current_user), session: Session = Depends(get_session)):
    """What to offer for the token under the caret. An empty bar gets recent
    searches, then saved views."""
    return suggest(session, user.id, q, cursor)


@router.post("/search/parse", response_model=ParseOut)
def search_parse(body: ParseIn, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """The bar's text as chips. A query that does not parse answers 200 with
    `ok: false` and the error, so the bar can underline it while typing."""
    try:
        query = parse(body.q)
        compile_filter(session, user.id, query)
    except QueryError as exc:
        return {"ok": False, "error": exc.as_dict()}
    return {"ok": True, "canonical": to_string(query), "chips": query.chips, "has_text": bool(query.rank_text)}


@router.get("/search/recent", response_model=list[str])
def search_recent(user: User = Depends(current_user), session: Session = Depends(get_session)):
    return recent_searches(session, user.id)


def _view(session: Session, user: User, view_id: int) -> SavedView:
    view = session.get(SavedView, view_id)
    if view is None or view.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Saved view not found")
    return view


@router.get("/saved-views", response_model=list[SavedViewOut])
def list_views(user: User = Depends(current_user), session: Session = Depends(get_session)):
    return session.scalars(select(SavedView).where(SavedView.user_id == user.id).order_by(SavedView.name)).all()


@router.post("/saved-views", response_model=SavedViewOut, status_code=status.HTTP_201_CREATED)
def create_view(body: SavedViewIn, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Stores the canonical form of the query."""
    view = SavedView(user_id=user.id, name=body.name, query=to_string(_checked(session, user, body.query)), params=body.params)
    session.add(view)
    session.commit()
    return view


@router.patch("/saved-views/{view_id}", response_model=SavedViewOut)
def patch_view(view_id: int, body: SavedViewPatch, user: User = Depends(current_user), session: Session = Depends(get_session)):
    view = _view(session, user, view_id)
    if body.name is not None:
        view.name = body.name
    if body.query is not None:
        view.query = to_string(_checked(session, user, body.query))
    if "params" in body.model_fields_set:
        view.params = body.params
    session.commit()
    return view


@router.delete("/saved-views/{view_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_view(view_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    session.delete(_view(session, user, view_id))
    session.commit()
