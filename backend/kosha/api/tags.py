"""Tag tree: read with counts, create, rename, describe, move, merge, delete,
approve/reject, and attaching tags to items with a role."""
import re

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from kosha.auth import current_user
from kosha.config import DEFAULT_NAMESPACE
from kosha.db import get_session
from kosha.models import Item, ItemTag, Tag, User, tags_id_seq
from kosha.schemas import TagAttach, TagCreate, TagMerge, TagMove, TagNode, TagOut, TagPatch

router = APIRouter(tags=["tags"])


def slugify(name: str) -> str:
    return re.sub(r"[^\w]+", "-", name.strip().lower()).strip("-") or "tag"


def tag_out(tag: Tag) -> dict:
    return {
        "id": tag.id, "parent_id": tag.parent_id, "path_ids": str(tag.path), "name": tag.name, "slug": tag.slug,
        "description": tag.description, "status": tag.status, "origin": tag.origin, "namespace": tag.namespace,
        "color": tag.color, "sort": tag.sort,
    }


def get_tag(session: Session, user: User, tag_id: int) -> Tag:
    tag = session.get(Tag, tag_id)
    if tag is None or tag.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tag not found")
    return tag


def get_item(session: Session, user: User, item_id: int) -> Item:
    item = session.get(Item, item_id)
    if item is None or item.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")
    return item


def _commit(session: Session) -> None:
    """Commit, turning a sibling-name clash into a 409."""
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "A tag with this name already exists here") from None


def is_within(path: str, ancestor_path: str) -> bool:
    return path == ancestor_path or path.startswith(ancestor_path + ".")


def move_tag(session: Session, tag: Tag, new_parent: Tag | None) -> None:
    """Re-parent a tag and rewrite the ltree path of everything beneath it, so
    subtree filters keep working. Items point at tag ids and are untouched."""
    if new_parent is not None and is_within(str(new_parent.path), str(tag.path)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A tag cannot be moved beneath itself")
    if new_parent is not None and new_parent.namespace != tag.namespace:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Tags are in different namespaces")
    old_path = str(tag.path)
    new_path = f"{new_parent.path}.{tag.id}" if new_parent else str(tag.id)
    session.flush()
    session.execute(text("""
        UPDATE tags
        SET path = CAST(:new AS ltree) || subpath(path, nlevel(CAST(:old AS ltree)))
        WHERE path <@ CAST(:old AS ltree) AND id != :id
    """), {"new": new_path, "old": old_path, "id": tag.id})
    tag.parent_id = new_parent.id if new_parent else None
    tag.path = new_path
    session.flush()
    session.expire_all()  # descendants loaded earlier hold stale paths


def attach_tag(session: Session, item_id: int, tag: Tag, role: str = "other", attached_by: str = "human") -> None:
    """Attach (or re-attach) a tag with a role. An item has at most one primary
    and one secondary tag: giving the role to a new tag demotes the old holder
    to "other"."""
    if role != "other":
        session.execute(
            update(ItemTag)
            .where(ItemTag.item_id == item_id, ItemTag.namespace == tag.namespace,
                   ItemTag.role == role, ItemTag.tag_id != tag.id)
            .values(role="other")
        )
    row = session.get(ItemTag, (item_id, tag.id))
    if row is None:
        session.add(ItemTag(item_id=item_id, tag_id=tag.id, namespace=tag.namespace, role=role, attached_by=attached_by))
    else:
        row.role, row.removed_by_user = role, False
    session.flush()


def detach_tag(session: Session, item_id: int, tag_id: int) -> None:
    """Removal is recorded, not deleted, so an agent does not re-attach it."""
    row = session.get(ItemTag, (item_id, tag_id))
    if row is not None:
        row.removed_by_user, row.role = True, "other"
        session.flush()


# --- Routes -----------------------------------------------------------------
@router.get("/tags", response_model=list[TagNode])
def tag_tree(
    namespace: str = DEFAULT_NAMESPACE,
    status_: str | None = Query(None, alias="status"),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """The tag tree with item counts (saved, not archived). `count` includes
    everything beneath a tag; `own_count` only the tag itself."""
    counts = {row.id: row for row in session.execute(text("""
        SELECT t.id,
               count(DISTINCT it.item_id) FILTER (WHERE d.id = t.id) AS own_count,
               count(DISTINCT it.item_id) AS count
        FROM tags t
        JOIN tags d ON d.user_id = t.user_id AND d.path <@ t.path
        JOIN item_tags it ON it.tag_id = d.id AND NOT it.removed_by_user
        JOIN items i ON i.id = it.item_id AND i.is_saved AND i.archived_at IS NULL
        WHERE t.user_id = :user_id AND t.namespace = :namespace
        GROUP BY t.id
    """), {"user_id": user.id, "namespace": namespace})}
    stmt = select(Tag).where(Tag.user_id == user.id, Tag.namespace == namespace).order_by(Tag.sort, Tag.name)
    if status_:
        stmt = stmt.where(Tag.status == status_)
    nodes = {}
    for tag in session.scalars(stmt):
        c = counts.get(tag.id)
        nodes[tag.id] = {**tag_out(tag), "own_count": c.own_count if c else 0, "count": c.count if c else 0, "children": []}
    roots = []
    for node in nodes.values():
        # With a status filter a parent may be absent; such tags surface as roots.
        (nodes[node["parent_id"]]["children"] if node["parent_id"] in nodes else roots).append(node)
    return roots


@router.post("/tags", response_model=TagOut, status_code=status.HTTP_201_CREATED)
def create_tag(body: TagCreate, user: User = Depends(current_user), session: Session = Depends(get_session)):
    parent = get_tag(session, user, body.parent_id) if body.parent_id else None
    tag_id = session.scalar(tags_id_seq)
    tag = Tag(
        id=tag_id, user_id=user.id, parent_id=parent.id if parent else None,
        path=f"{parent.path}.{tag_id}" if parent else str(tag_id),
        name=body.name.strip(), slug=slugify(body.name), description=body.description, color=body.color,
        namespace=parent.namespace if parent else (body.namespace or DEFAULT_NAMESPACE),
        status=body.status, origin=body.origin,
    )
    session.add(tag)
    _commit(session)
    return tag_out(tag)


@router.patch("/tags/{tag_id}", response_model=TagOut)
def update_tag(tag_id: int, body: TagPatch, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Rename, describe, recolour or reorder. A rename changes no path."""
    tag = get_tag(session, user, tag_id)
    changes = body.model_dump(exclude_unset=True)
    if changes.get("name"):
        tag.name, tag.slug = changes.pop("name").strip(), slugify(body.name)
    for name, value in changes.items():
        setattr(tag, name, value)
    _commit(session)
    return tag_out(tag)


@router.post("/tags/{tag_id}/move", response_model=TagOut)
def move(tag_id: int, body: TagMove, user: User = Depends(current_user), session: Session = Depends(get_session)):
    tag = get_tag(session, user, tag_id)
    try:
        move_tag(session, tag, get_tag(session, user, body.parent_id) if body.parent_id else None)
    except IntegrityError:
        session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "A tag with this name already exists there") from None
    _commit(session)
    return tag_out(get_tag(session, user, tag_id))


def merge_tags(session: Session, user: User, tag: Tag, into: Tag, restore_removed: bool = True) -> None:
    """Fold `tag` into `into` and commit. `restore_removed` is the owner's
    merge: an item they removed the target from, but which carries the merged
    tag, carries the target again. An agent passes False: a removal stands."""
    tag_id, into_id = tag.id, into.id
    if is_within(str(into.path), str(tag.path)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot merge a tag into itself or its own subtree")
    if into.namespace != tag.namespace:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Tags are in different namespaces")
    params = {"a": tag.id, "b": into.id}
    if restore_removed:
        session.execute(text("""
            UPDATE item_tags b SET removed_by_user = false, role = 'other' FROM item_tags a
            WHERE a.tag_id = :a AND b.tag_id = :b AND a.item_id = b.item_id
              AND b.removed_by_user AND NOT a.removed_by_user
        """), params)
    session.execute(text("""
        DELETE FROM item_tags a USING item_tags b
        WHERE a.tag_id = :a AND b.tag_id = :b AND a.item_id = b.item_id
    """), params)
    session.execute(text("UPDATE item_tags SET tag_id = :b WHERE tag_id = :a"), params)
    try:
        for child in session.scalars(select(Tag).where(Tag.parent_id == tag.id)).all():
            move_tag(session, child, into)
            into = get_tag(session, user, into_id)  # move_tag expires loaded rows
    except IntegrityError:
        session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "A child tag with the same name exists in the target") from None
    session.delete(get_tag(session, user, tag_id))
    _commit(session)


@router.post("/tags/{tag_id}/merge", response_model=TagOut)
def merge(tag_id: int, body: TagMerge, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Fold this tag into another: its items and child tags move over, then it
    is deleted. Where an item carries both, the target's row is kept."""
    merge_tags(session, user, get_tag(session, user, tag_id), get_tag(session, user, body.into_id))
    return tag_out(get_tag(session, user, body.into_id))


@router.delete("/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tag(tag_id: int, recursive: bool = False, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Delete a tag and detach it from its items. A tag with children is only
    deleted (with its whole subtree) when `recursive=true`."""
    tag = get_tag(session, user, tag_id)
    if not recursive and session.scalar(select(Tag.id).where(Tag.parent_id == tag.id).limit(1)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Tag has child tags; pass recursive=true to delete them too")
    session.delete(tag)  # child tags and item_tags go by ON DELETE CASCADE
    session.commit()


def _set_status(tag_id: int, new_status: str, user: User, session: Session) -> dict:
    tag = get_tag(session, user, tag_id)
    tag.status = new_status
    session.commit()
    return tag_out(tag)


@router.post("/tags/{tag_id}/approve", response_model=TagOut)
def approve(tag_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return _set_status(tag_id, "approved", user, session)


@router.post("/tags/{tag_id}/reject", response_model=TagOut)
def reject(tag_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Rejected tags are kept so they are not proposed again."""
    return _set_status(tag_id, "rejected", user, session)


@router.put("/items/{item_id}/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
def attach(item_id: int, tag_id: int, body: TagAttach | None = None, user: User = Depends(current_user), session: Session = Depends(get_session)):
    get_item(session, user, item_id)
    attach_tag(session, item_id, get_tag(session, user, tag_id), (body or TagAttach()).role)
    session.commit()


@router.delete("/items/{item_id}/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
def detach(item_id: int, tag_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    get_item(session, user, item_id)
    get_tag(session, user, tag_id)
    detach_tag(session, item_id, tag_id)
    session.commit()
