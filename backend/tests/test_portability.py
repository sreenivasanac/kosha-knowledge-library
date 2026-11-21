"""Import (Netscape HTML, Raindrop CSV, generic CSV) and export (CSV, HTML, JSON)."""
import csv
import io
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from kosha import add_item, importers
from kosha.api import portability
from kosha.models import Collection, Item, ItemCollection, ItemMedia, Tag
from tests.routes_fix import ensure_routes

ensure_routes(portability.router)
FIXTURES = Path(__file__).parent / "fixtures"


def upload(client, name: str, **params):
    return client.post("/api/import", files={"file": (name, (FIXTURES / name).read_bytes())}, params=params)


def test_parse_netscape():
    format, rows = importers.parse((FIXTURES / "bookmarks_netscape.html").read_bytes())
    assert format == "netscape" and len(rows) == 8
    assert (rows[0].title, rows[0].folder, rows[0].created) == ("Top level & loose", None, datetime.fromtimestamp(1700000000, UTC))
    hnsw = rows[1]
    assert (hnsw.folder, hnsw.tags, hnsw.excerpt) == ("Reading", ["databases", "AI/RAG"], "Notes on m and ef_construction for a small index.")
    assert (rows[2].title, rows[2].folder) == ("Attention Is All You Need", "Reading/Papers")
    assert rows[3].folder == "Reading" and rows[4].folder is None  # back out of the nested folders


def test_classify():
    assert importers.classify("https://twitter.com/a/status/42?s=20") == ("x", "42")
    assert importers.classify("https://www.linkedin.com/feed/update/urn:li:activity:7123/") == ("linkedin", "urn:li:activity:7123")
    assert importers.classify("https://x.com/someone")[0] == "web" and importers.classify("https://www.linkedin.com/in/someone")[0] == "web"


def test_import_netscape(client, session, user, monkeypatch):
    monkeypatch.setattr(add_item.httpx, "Client", lambda *a, **k: (_ for _ in ()).throw(AssertionError("an import must not fetch")))
    preview = upload(client, "bookmarks_netscape.html", dry_run="true").json()
    assert preview["format"] == "netscape" and (preview["created"], preview["duplicates"], preview["skipped"]) == (5, 1, 2)
    assert len(preview["rows"]) == 8 and preview["rows"][1]["tags"] == ["databases", "AI/RAG"]
    assert session.scalar(select(Item.id)) is None  # a dry run writes nothing

    done = upload(client, "bookmarks_netscape.html").json()
    assert (done["created"], done["duplicates"], done["skipped"]) == (5, 1, 2) and "rows" not in done
    assert len(done["errors"]) == 2 and "row 7" in done["errors"][0] and "javascript" in done["errors"][0]
    items = {i.url: i for i in session.scalars(select(Item))}
    hnsw = items["https://example.org/pgvector-hnsw"]
    assert (hnsw.source, hnsw.title, hnsw.body) == ("web", "Tuning HNSW in pgvector", "Notes on m and ef_construction for a small index.")
    assert hnsw.saved_at == datetime.fromtimestamp(1700000100, UTC) and hnsw.summary and "hnsw" in hnsw.search_text.lower()
    assert (items["https://x.com/someone/status/1234567890123456789"].source,
            items["https://x.com/someone/status/1234567890123456789"].source_id) == ("x", "1234567890123456789")
    linkedin = next(i for i in items.values() if i.source == "linkedin")
    assert linkedin.source_id == "urn:li:activity:7123456789012345678"
    # Folders are collections, tags are approved tags of the owner with their paths.
    folders = dict(session.execute(select(Collection.name, Collection.kind)).all())
    assert folders == {"Reading": "browser_folder", "Reading/Papers": "browser_folder"}
    assert session.scalar(select(Collection.name).join(ItemCollection).where(ItemCollection.item_id == hnsw.id)) == "Reading"
    tags = {t.name: t for t in session.scalars(select(Tag))}
    assert set(tags) == {"databases", "AI", "RAG"} and tags["RAG"].parent_id == tags["AI"].id
    assert {(t.status, t.origin) for t in tags.values()} == {("approved", "import")}
    listed = client.get("/api/items", params={"q": "#AI/RAG"}).json()["items"]
    assert [i["url"] for i in listed] == ["https://example.org/pgvector-hnsw"]
    assert client.get("/api/items", params={"q": "hnsw"}).json()["items"][0]["id"] == hnsw.id  # search text was built

    again = upload(client, "bookmarks_netscape.html").json()
    assert (again["created"], again["duplicates"], again["skipped"]) == (0, 6, 2)
    # An item deleted for good does not come back.
    client.delete(f"/api/items/{hnsw.id}")
    again = upload(client, "bookmarks_netscape.html").json()
    assert (again["created"], again["duplicates"], again["skipped"]) == (0, 5, 3) and "deleted" in again["errors"][-1]


def test_import_csv(client, session, user):
    done = upload(client, "raindrop.csv").json()
    assert done["format"] == "raindrop_csv" and (done["created"], done["duplicates"], done["skipped"]) == (2, 0, 1)
    postgres = session.scalar(select(Item).where(Item.url == "https://example.com/postgres-good-parts"))
    assert (postgres.title, postgres.note, postgres.body, postgres.starred) == (
        "Postgres, the good parts", "Read before the migration", "A tour of features people overlook.", True)
    assert postgres.saved_at == datetime(2024, 3, 1, 10, tzinfo=UTC)
    assert session.scalar(select(ItemMedia.original_url).where(ItemMedia.item_id == postgres.id)) == "https://example.com/cover.png"
    assert client.get("/api/items", params={"q": "migration"}).json()["items"][0]["id"] == postgres.id  # the note is searchable
    assert {c.name for c in session.scalars(select(Collection))} == {"Tech/Databases", "Cooking"}

    done = upload(client, "links.csv", format="csv").json()  # header names in any case; a Unix date
    assert done["format"] == "csv" and (done["created"], done["duplicates"], done["skipped"]) == (2, 1, 1)
    assert session.scalar(select(Item.saved_at).where(Item.url == "https://example.net/one")) == datetime.fromtimestamp(1700000000, UTC)
    # The same URL added by hand is the same item.
    assert importers.classify("https://example.net/two")[1] == session.scalar(select(Item.source_id).where(Item.url == "https://example.net/two"))


def test_import_refusals(client, user, monkeypatch):
    assert client.post("/api/import", files={"file": ("x.csv", b"name,link\na,b\n")}).status_code == 422
    assert client.post("/api/import", files={"file": ("x.txt", b"just some words")}).status_code == 422
    assert upload(client, "raindrop.csv", format="opml").status_code == 422
    monkeypatch.setattr(importers, "IMPORT_MAX_BYTES", 100)
    assert upload(client, "raindrop.csv").status_code == 413


def test_export(client, session, user):
    upload(client, "bookmarks_netscape.html")
    upload(client, "raindrop.csv")
    rows = list(csv.DictReader(io.StringIO(client.get("/api/export", params={"format": "csv"}).text)))
    assert len(rows) == 7 and tuple(rows[0]) == ("id", "title", "note", "excerpt", "url", "folder", "tags", "created", "cover", "highlights", "favorite")
    postgres = next(r for r in rows if r["url"] == "https://example.com/postgres-good-parts")
    assert (postgres["folder"], postgres["tags"], postgres["favorite"], postgres["note"]) == (
        "Tech/Databases", "databases, postgres", "true", "Read before the migration")
    assert postgres["created"].startswith("2024-03-01T10:00:00") and postgres["cover"] == "https://example.com/cover.png"

    filtered = client.get("/api/export", params={"format": "json", "q": "#AI"})
    assert filtered.headers["content-disposition"].endswith('.json"')
    items = filtered.json()["items"]
    assert sorted(i["title"] for i in items) == ["Attention Is All You Need", "Tuning HNSW in pgvector"]
    assert "raw" not in items[0] and "embedding" not in str(items[0].keys()) and items[0]["tags"][0]["path"]
    assert client.get("/api/export", params={"q": "nosuchqualifier:1"}).status_code == 422
    assert client.get("/api/export", params={"format": "xml"}).status_code == 422

    page = client.get("/api/export", params={"format": "html"}).text
    assert page.startswith("<!DOCTYPE NETSCAPE-Bookmark-file-1>") and 'TAGS="AI/RAG,databases"' in page and "Top level &amp; loose" in page
    # What Kosha exports, Kosha imports: the same rows, folders and tag paths.
    _, back = importers.parse(page.encode())
    assert len(back) == 7
    hnsw = next(r for r in back if r.url == "https://example.org/pgvector-hnsw")
    assert (hnsw.folder, hnsw.tags, hnsw.created) == ("Reading", ["AI/RAG", "databases"], datetime.fromtimestamp(1700000100, UTC))
    _, back = importers.parse(client.get("/api/export", params={"format": "csv"}).content)
    assert {r.url: r.folder for r in back}["https://arxiv.org/abs/1706.03762"] == "Reading/Papers"
