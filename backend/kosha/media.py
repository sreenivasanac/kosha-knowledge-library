"""Media store: download photos and previews (and videos when the setting is
on) to the media storage (kosha/storage.py: a folder or an S3-compatible
bucket), and serve them.

Resumable by construction: a row is done when its path column is set, files
are written to `<name>.part` and renamed, so an interrupted run just continues.
"""
import re
import tempfile
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from kosha import storage
from kosha.auth import current_user
from kosha.config import (
    MEDIA_ALLOWED_HOSTS,
    MEDIA_CONCURRENCY,
    MEDIA_MAX_BYTES_IMAGE,
    MEDIA_MAX_BYTES_VIDEO,
    MEDIA_MAX_REDIRECTS,
    MEDIA_TIMEOUT_S,
)
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


def allowed_url(url: str) -> bool:
    """https, and a known media host or one of its subdomains."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and any(host == h or host.endswith("." + h) for h in MEDIA_ALLOWED_HOSTS)


def _download(client: httpx.Client, url: str, dest: Path, max_bytes: int) -> int:
    """Fetch one file. Redirects are followed by hand so every hop is checked
    against the allow-list, and the download stops at `max_bytes`."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    for _ in range(MEDIA_MAX_REDIRECTS + 1):
        if not allowed_url(url):
            raise OSError(f"Not an allowed media address: {urlparse(url).hostname}")
        with client.stream("GET", url, follow_redirects=False) as response:
            if response.is_redirect:
                url = urljoin(url, response.headers.get("location", ""))
                continue
            response.raise_for_status()
            size = 0
            try:
                with part.open("wb") as fh:
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > max_bytes:
                            raise OSError(f"Larger than {max_bytes} bytes")
                        fh.write(chunk)
            except OSError:
                part.unlink(missing_ok=True)
                raise
        part.replace(dest)
        return size
    raise OSError("Too many redirects")


def _store(client: httpx.Client, url: str, store, key: str, max_bytes: int, folder: Path) -> int:
    """Download one file to `folder`, then hand it to the storage under `key`."""
    temp = folder / key.replace("/", "_")
    try:
        size = _download(client, url, temp, max_bytes)
        store.put_file(key, temp)
    finally:
        temp.unlink(missing_ok=True)
    return size


def fetch_media(session: Session, user_id: int, limit: int | None = None, client: httpx.Client | None = None) -> dict:
    """Download what the settings ask for and is not stored yet. Previews are
    always stored. Returns counts; failures are left for the next run."""
    settings = load_settings(session, user_id)
    store = storage.get_storage(session)
    rows = session.scalars(
        select(ItemMedia).join(Item, Item.id == ItemMedia.item_id).where(
            Item.user_id == user_id,
            (ItemMedia.local_path.is_(None) & ItemMedia.original_url.is_not(None))
            | (ItemMedia.local_preview_path.is_(None) & ItemMedia.preview_url.is_not(None)),
        ).order_by(ItemMedia.id)
    ).all()
    jobs = []  # (media row, column to set, url, relative path, size cap)
    cap = lambda m: MEDIA_MAX_BYTES_VIDEO if m.type in ("video", "audio") else MEDIA_MAX_BYTES_IMAGE  # noqa: E731
    for m in rows:
        if m.local_path is None and m.original_url and settings.get(STORE_SETTING.get(m.type, "store_images")):
            url = twitter_original(m.original_url) if m.type == "photo" else m.original_url
            jobs.append((m, "local_path", url, f"{m.item_id}/{m.id}{file_extension(url)}", cap(m)))
        if m.local_preview_path is None and m.preview_url:
            jobs.append((m, "local_preview_path", m.preview_url,
                         f"{m.item_id}/{m.id}-preview{file_extension(m.preview_url)}", MEDIA_MAX_BYTES_IMAGE))
    jobs = [j for j in jobs if allowed_url(j[2])][:limit]

    counts = {"downloaded": 0, "failed": 0, "bytes": 0}
    own_client = client is None
    client = client or httpx.Client(timeout=MEDIA_TIMEOUT_S)
    # ponytail: a file that fails (deleted at the source) is retried every run;
    # add a failure counter column if those pile up.
    try:
        with tempfile.TemporaryDirectory(prefix="kosha-media-") as folder, ThreadPoolExecutor(MEDIA_CONCURRENCY) as pool:
            futures = {pool.submit(_store, client, url, store, rel, max_bytes, Path(folder)): (m, column, rel)
                       for m, column, url, rel, max_bytes in jobs}
            for done, future in enumerate(as_completed(futures), 1):
                m, column, rel = futures[future]
                try:
                    size = future.result()
                except (httpx.HTTPError, *storage.ERRORS):
                    counts["failed"] += 1
                    continue
                setattr(m, column, rel)  # the storage key: relative to the media folder or the bucket prefix
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
def serve_media(media_id: int, request: Request, preview: bool = False, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """The stored file when there is one, with `Range` support so a video can
    seek (from disk, streamed from the bucket, or a redirect to a presigned
    address, as the storage is configured); otherwise a redirect to the remote
    address (which stops working if the source removes the file)."""
    media = session.get(ItemMedia, media_id)
    if media is None or session.get(Item, media.item_id).user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Media not found")
    local = media.local_preview_path if preview else media.local_path
    if local:
        try:
            stored = storage.response(session, local, request.headers.get("range"), None if preview else media.mime_type)
        except storage.StorageError:  # a key that points outside the storage
            stored = None
        if stored is not None:
            return stored
    remote = (media.preview_url or media.original_url) if preview else media.original_url
    if not remote:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No file for this media")
    return RedirectResponse(remote)
