"""Media store: download photos and previews (and videos when the setting is
on) to the media folder, and serve them.

Resumable by construction: a row is done when its path column is set, files
are written to `<name>.part` and renamed, so an interrupted run just continues.
"""
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from kosha.auth import current_user
from kosha.config import MEDIA_CONCURRENCY, MEDIA_TIMEOUT_S, get_settings
from kosha.db import get_session
from kosha.models import Item, ItemMedia, User
from kosha.prefs import load_settings

# Which setting decides whether a media type's original file is stored.
STORE_SETTING = {
    "photo": "store_images", "animated_gif": "store_images", "document": "store_images",
    "video": "store_videos", "audio": "store_videos",
}


def twitter_original(url: str) -> str:
    """Full-size address of a pbs.twimg.com photo (ported from
    twitter-web-exporter's formatTwitterImage, MIT)."""
    match = re.match(r"^(https?://pbs\.twimg\.com/media/.+)\.(\w+)$", url)
    return f"{match.group(1)}?format={match.group(2)}&name=orig" if match else url


def file_extension(url: str) -> str:
    match = re.search(r"format=(\w+)|\.(\w{2,5})(?:\?.*)?$", url)
    return "." + (match.group(1) or match.group(2)).lower() if match else ".bin"


def _download(client: httpx.Client, url: str, dest: Path) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    with client.stream("GET", url, follow_redirects=True) as response:
        response.raise_for_status()
        with part.open("wb") as fh:
            for chunk in response.iter_bytes():
                fh.write(chunk)
    part.replace(dest)
    return dest.stat().st_size


def fetch_media(session: Session, user_id: int, limit: int | None = None, client: httpx.Client | None = None) -> dict:
    """Download what the settings ask for and is not stored yet. Previews are
    always stored. Returns counts; failures are left for the next run."""
    settings = load_settings(session, user_id)
    root = get_settings().kosha_media_dir
    rows = session.scalars(
        select(ItemMedia).join(Item, Item.id == ItemMedia.item_id).where(
            Item.user_id == user_id,
            (ItemMedia.local_path.is_(None) & ItemMedia.original_url.is_not(None))
            | (ItemMedia.local_preview_path.is_(None) & ItemMedia.preview_url.is_not(None)),
        ).order_by(ItemMedia.id)
    ).all()
    jobs = []  # (media row, column to set, url, relative path)
    for m in rows:
        if m.local_path is None and m.original_url and settings.get(STORE_SETTING.get(m.type, "store_images")):
            url = twitter_original(m.original_url) if m.type == "photo" else m.original_url
            jobs.append((m, "local_path", url, f"{m.item_id}/{m.id}{file_extension(url)}"))
        if m.local_preview_path is None and m.preview_url:
            jobs.append((m, "local_preview_path", m.preview_url, f"{m.item_id}/{m.id}-preview{file_extension(m.preview_url)}"))
    jobs = [j for j in jobs if j[2].startswith("https://")][:limit]

    counts = {"downloaded": 0, "failed": 0, "bytes": 0}
    own_client = client is None
    client = client or httpx.Client(timeout=MEDIA_TIMEOUT_S)
    # ponytail: a file that fails (deleted at the source) is retried every run;
    # add a failure counter column if those pile up.
    try:
        with ThreadPoolExecutor(MEDIA_CONCURRENCY) as pool:
            futures = {pool.submit(_download, client, url, root / rel): (m, column, rel) for m, column, url, rel in jobs}
            for done, future in enumerate(as_completed(futures), 1):
                m, column, rel = futures[future]
                try:
                    size = future.result()
                except (httpx.HTTPError, OSError):
                    counts["failed"] += 1
                    continue
                setattr(m, column, rel)  # stored relative to the media folder
                if column == "local_path":
                    m.bytes = size
                counts["downloaded"] += 1
                counts["bytes"] += size
                if done % 50 == 0:
                    session.commit()  # keep progress if the run is interrupted
        session.commit()
    finally:
        if own_client:
            client.close()
    return counts


router = APIRouter(tags=["media"])


@router.get("/media/{media_id}")
def serve_media(media_id: int, preview: bool = False, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """The stored file when there is one; otherwise a redirect to the remote
    address (which stops working if the source removes the file)."""
    media = session.get(ItemMedia, media_id)
    if media is None or session.get(Item, media.item_id).user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Media not found")
    root = get_settings().kosha_media_dir.resolve()
    local = media.local_preview_path if preview else media.local_path
    if local:
        path = (root / local).resolve()
        if path.is_relative_to(root) and path.is_file():
            return FileResponse(path, media_type=None if preview else media.mime_type)
    remote = (media.preview_url or media.original_url) if preview else media.original_url
    if not remote:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No file for this media")
    return RedirectResponse(remote)
