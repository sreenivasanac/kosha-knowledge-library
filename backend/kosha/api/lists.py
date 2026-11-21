"""Hand-made lists: ordered sets of items that never expire."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from kosha.auth import current_user
from kosha.db import get_session
from kosha.models import Item, List, ListItem, User
from kosha.schemas import ListDetailOut, ListIn, ListItemsIn, ListOut
from kosha.serialize import serialize_items

router = APIRouter(tags=["lists"])


def _list(session: Session, user: User, list_id: int) -> List:
    row = session.get(List, list_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "List not found")
    return row


def _out(session: Session, row: List) -> dict:
    count = session.scalar(select(func.count()).select_from(ListItem).where(ListItem.list_id == row.id))
    return {"id": row.id, "name": row.name, "description": row.description, "created_at": row.created_at, "count": count}


@router.get("/lists", response_model=list[ListOut])
def all_lists(user: User = Depends(current_user), session: Session = Depends(get_session)):
    return [_out(session, row) for row in session.scalars(select(List).where(List.user_id == user.id).order_by(List.name))]


@router.post("/lists", response_model=ListOut, status_code=status.HTTP_201_CREATED)
def create_list(body: ListIn, user: User = Depends(current_user), session: Session = Depends(get_session)):
    row = List(user_id=user.id, name=body.name, description=body.description)
    session.add(row)
    session.commit()
    return _out(session, row)


@router.get("/lists/{list_id}", response_model=ListDetailOut)
def one_list(list_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    row = _list(session, user, list_id)
    items = session.scalars(select(Item).join(ListItem, ListItem.item_id == Item.id).where(ListItem.list_id == row.id)
                            .order_by(ListItem.position.asc().nulls_last(), ListItem.added_at, Item.id)).all()
    return {**_out(session, row), "items": serialize_items(session, user.id, list(items))}


@router.post("/lists/{list_id}/items", response_model=ListOut)
def add_items(list_id: int, body: ListItemsIn, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Append items in the order given; ones already in the list stay where they are."""
    row = _list(session, user, list_id)
    owned = set(session.scalars(select(Item.id).where(Item.user_id == user.id, Item.id.in_(body.item_ids))))
    present = set(session.scalars(select(ListItem.item_id).where(ListItem.list_id == row.id)))
    position = session.scalar(select(func.coalesce(func.max(ListItem.position), 0)).where(ListItem.list_id == row.id))
    for item_id in dict.fromkeys(body.item_ids):
        if item_id in owned and item_id not in present:
            position += 1
            session.add(ListItem(list_id=row.id, item_id=item_id, position=position))
    session.commit()
    return _out(session, row)


@router.delete("/lists/{list_id}/items", response_model=ListOut)
def remove_items(list_id: int, body: ListItemsIn, user: User = Depends(current_user), session: Session = Depends(get_session)):
    row = _list(session, user, list_id)
    session.execute(delete(ListItem).where(ListItem.list_id == row.id, ListItem.item_id.in_(body.item_ids)))
    session.commit()
    return _out(session, row)


@router.put("/lists/{list_id}/order", response_model=ListOut)
def reorder(list_id: int, body: ListItemsIn, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Set the order: the given ids first, in that order; members not named keep
    their relative order after them."""
    row = _list(session, user, list_id)
    members = session.scalars(select(ListItem).where(ListItem.list_id == row.id)
                              .order_by(ListItem.position.asc().nulls_last(), ListItem.added_at)).all()
    rank = {item_id: n for n, item_id in enumerate(dict.fromkeys(body.item_ids))}
    members.sort(key=lambda m: rank.get(m.item_id, len(rank)))  # stable: the rest keep their order
    for position, member in enumerate(members, start=1):
        member.position = position
    session.commit()
    return _out(session, row)
