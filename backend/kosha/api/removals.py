"""Removal at the source: the extension asks which archived items to
un-bookmark, does it in the browser, and reports back."""
import logging

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from kosha.api.items import delete_item
from kosha.api.tags import get_item
from kosha.auth import current_user
from kosha.db import get_session
from kosha.models import Item, User
from kosha.schemas import RemovalOut, RemovalResultIn

router = APIRouter(tags=["removals"])
log = logging.getLogger(__name__)


@router.get("/removals/pending", response_model=list[RemovalOut])
def pending(user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Archived saved items, oldest archive first. `source_id` is the source's
    own id: the numeric post id for X, `urn:li:activity:…` for LinkedIn."""
    rows = session.scalars(select(Item).where(Item.user_id == user.id, Item.is_saved, Item.archived_at.is_not(None))
                           .order_by(Item.archived_at, Item.id))
    return [{"item_id": i.id, "source": i.source, "source_id": i.source_id, "url": i.url} for i in rows]


@router.post("/removals/result", status_code=status.HTTP_200_OK)
def result(body: RemovalResultIn, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """`removed` and `already_gone` hard-delete the item (recording it in
    `deleted_items`); `failed` leaves it archived for the next attempt."""
    item = get_item(session, user, body.item_id)
    if body.status == "failed":
        log.warning("Removal at source failed for item %s (%s): %s", item.id, item.source, body.detail)
        return {"item_id": body.item_id, "deleted": False}
    delete_item(session, user, item)
    return {"item_id": body.item_id, "deleted": True}
