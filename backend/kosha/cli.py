"""The `kosha` command line."""
from pathlib import Path

import typer
from sqlalchemy import select

from kosha.config import APP_NAME, BACKEND_DIR, SERVER_HOST, SERVER_PORT, get_settings

TRUSTED_PROXY_IPS = "127.0.0.1"  # reverse proxies whose forwarding headers `kosha serve` believes

app = typer.Typer(help=f"{APP_NAME} backend", no_args_is_help=True, add_completion=False)


USER_OPTION = typer.Option(None, "--user", help="Email of the account to work on. Default: the admin (KOSHA_ADMIN_EMAIL, else the first admin)")


def _user(session, email: str | None = None):
    """The account a command works on, or exit with a hint."""
    from sqlalchemy import func

    from kosha.models import User

    email = email or get_settings().kosha_admin_email
    if email:
        user = session.scalar(select(User).where(func.lower(User.email) == email.strip().lower()))
    else:
        user = session.scalar(select(User).where(User.role == "admin").order_by(User.id).limit(1))
    if user is None:
        typer.echo(f"No user {email}." if email else "No admin user yet. Run: kosha create-admin, or open the web app and set it up.", err=True)
        raise typer.Exit(1)
    return user


@app.command()
def serve(host: str = SERVER_HOST, port: int = SERVER_PORT, reload: bool = False):
    """Run the API server."""
    import uvicorn

    from kosha.sync.scheduler import start_scheduler

    start_scheduler()
    # X-Forwarded-For/-Proto are honoured from a reverse proxy on this machine
    # only, so the login limiter sees the real client address behind nginx.
    uvicorn.run("kosha.app:app", host=host, port=port, reload=reload, proxy_headers=True, forwarded_allow_ips=TRUSTED_PROXY_IPS)


@app.command()
def migrate():
    """Bring the database schema up to date."""
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config(str(BACKEND_DIR / "alembic.ini")), "head")
    typer.echo("Database is up to date.")


@app.command("create-admin")
def create_admin():
    """Create or update the admin user and API token from the environment.
    Without KOSHA_ADMIN_EMAIL / KOSHA_ADMIN_PASSWORD it does nothing: the first
    account is then created on the web app's setup screen."""
    from kosha.auth import create_admin as create
    from kosha.db import SessionLocal

    env = get_settings()
    if env.kosha_demo or not env.kosha_admin_email or not env.kosha_admin_password:
        typer.echo("This is a demo instance: its one account comes from kosha demo-load." if env.kosha_demo else
                   "KOSHA_ADMIN_EMAIL and KOSHA_ADMIN_PASSWORD are not set: create the first account on the web app's setup screen.")
        return
    with SessionLocal() as session:
        create(session)
        session.commit()
    typer.echo("Admin user and API token are set.")


@app.command("ingest-dir")
def ingest_dir(path: Path, user_email: str = USER_OPTION):
    """Ingest every capture envelope (*.json) in a folder. Idempotent."""
    from kosha.db import SessionLocal
    from kosha.ingest import ingest_dir as ingest
    from kosha.models import Item
    from kosha.parsers.x_graphql import epoch_report

    with SessionLocal() as session:
        user = _user(session, user_email)
        totals = ingest(session, user.id, path)
        failed = totals.pop("failed")
        typer.echo(" ".join(f"{k}={v}" for k, v in totals.items()))
        for line in failed:
            typer.echo(f"FAILED {line}", err=True)
        # Which sortIndex epoch decodes to plausible save times on this library.
        pairs = session.execute(select(Item.saved_order, Item.posted_at).where(
            Item.user_id == user.id, Item.source == "x", Item.saved_order.is_not(None))).all()
        if pairs:
            typer.echo("sortIndex epoch check: " + " ".join(f"{k}={v}" for k, v in epoch_report([tuple(p) for p in pairs]).items()))
    if failed:
        raise typer.Exit(1)


@app.command("media-fetch")
def media_fetch(limit: int = typer.Option(None, help="Stop after this many files"), user_email: str = USER_OPTION):
    """Download media the settings ask for (photos and previews by default)."""
    from kosha.db import SessionLocal
    from kosha.media import fetch_media

    with SessionLocal() as session:
        counts = fetch_media(session, _user(session, user_email).id, limit=limit)
    typer.echo(" ".join(f"{k}={v}" for k, v in counts.items()))


@app.command("reindex")
def reindex_cmd():
    """Rewrite the search columns (search text and text-search vectors) of every item."""
    from kosha.db import SessionLocal
    from kosha.search.index import reindex

    with SessionLocal() as session:
        rows = reindex(session)
        session.commit()
    typer.echo(f"reindexed={rows}")


@app.command()
def summarize(user_email: str = USER_OPTION):
    """Write rule-built summaries for items that lack one or whose text changed."""
    from kosha.db import SessionLocal
    from kosha.summaries import summarize as run

    with SessionLocal() as session:
        counts = run(session, _user(session, user_email).id)
    typer.echo(" ".join(f"{k}={v}" for k, v in counts.items()))


@app.command()
def embed(limit: int = typer.Option(None, help="Stop after this many items"), user_email: str = USER_OPTION):
    """Embed saved items whose text is new or changed (Voyage; resumable)."""
    from kosha.db import SessionLocal
    from kosha.search.embeddings import EmbeddingError, embed_items

    with SessionLocal() as session:
        try:
            totals = embed_items(session, _user(session, user_email).id, limit=limit, echo=typer.echo)
        except EmbeddingError as exc:  # no key, the monthly cap, or Voyage is down
            typer.echo(f"Cannot embed: {exc}", err=True)
            raise typer.Exit(1) from None
    typer.echo(" ".join(f"{k}={round(v, 4) if isinstance(v, float) else v}" for k, v in totals.items()))


@app.command("sync-x")
def sync_x(user_email: str = USER_OPTION):
    """Run the official X API bookmark sync once (does nothing unless the
    `x_api_sync_enabled` setting is on and the X token is configured; the
    server's X credentials are the admin's, so it runs for an admin only)."""
    from kosha.db import SessionLocal
    from kosha.sync.x_api import sync_bookmarks

    with SessionLocal() as session:
        summary = sync_bookmarks(session, _user(session, user_email).id)
    typer.echo(" ".join(f"{k}={v}" for k, v in summary.items()))


@app.command()
def reparse():
    """Parse again every stored capture whose parsing failed (after a parser fix)."""
    from kosha.db import SessionLocal
    from kosha.ingest import ingest_envelope
    from kosha.models import Capture

    fixed = failed = 0
    with SessionLocal() as session:
        for capture in session.scalars(select(Capture).where(Capture.parse_error.is_not(None)).order_by(Capture.id)).all():
            envelope = {"source": capture.source, "kind": capture.kind, "meta": capture.meta, "payload": capture.payload}
            ingest_envelope(session, capture.user_id, envelope)  # finds the same row by its payload hash
            session.commit()
            if capture.parse_error:
                failed += 1
                typer.echo(f"FAILED capture {capture.id}: {capture.parse_error[:200]}", err=True)
            else:
                fixed += 1
    typer.echo(f"fixed={fixed} failed={failed}")


# --- Media storage and imports -------------------------------------------------
@app.command("storage-test")
def storage_test():
    """Write, read, range-read and delete one small object on the active media storage."""
    from kosha import storage
    from kosha.db import SessionLocal

    with SessionLocal() as session:
        try:
            result = storage.self_test(storage.get_storage(session))
        except storage.StorageError as exc:  # not configured
            result = {"ok": False, "message": str(exc)}
    typer.echo(result["message"], err=not result["ok"])
    if not result["ok"]:
        raise typer.Exit(1)


@app.command("storage-migrate")
def storage_migrate(to: str = typer.Option(..., "--to", help="local or s3: the backend to copy every media file to and then switch to")):
    """Copy the media files to the other storage backend, check them, and make
    it the active one. The old copy is left in place. Safe to run again."""
    from kosha import storage
    from kosha.db import SessionLocal

    with SessionLocal() as session:
        try:
            state = storage.start_migration(session, to, background=False)
        except storage.StorageError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from None
    typer.echo(f"total={state['total']} copied={state['copied']} bytes={state['bytes']} failed={state['failed']}")
    for line in state["errors"]:
        typer.echo(f"FAILED {line}", err=True)
    typer.echo(state["message"], err=not state["switched"])
    if not state["switched"]:
        raise typer.Exit(1)


@app.command("import")
def import_file(file: Path, user_email: str = USER_OPTION,
                dry_run: bool = typer.Option(False, "--dry-run", help="Count what would be created; write nothing")):
    """Import a bookmarks file (browser/Pocket/Raindrop HTML export, or CSV) into a library."""
    from kosha import importers
    from kosha.db import SessionLocal

    with SessionLocal() as session:
        user = _user(session, user_email)
        try:
            format, rows = importers.parse(file.read_bytes())
        except (importers.ImportError_, OSError) as exc:
            typer.echo(f"Cannot import {file}: {exc}", err=True)
            raise typer.Exit(1) from None
        result = importers.run_import(session, user.id, rows, dry_run=dry_run)
    errors = result.pop("errors")
    typer.echo(("dry run: " if dry_run else "") + f"format={format} rows={len(rows)} " + " ".join(f"{k}={v}" for k, v in result.items()))
    for line in errors:
        typer.echo(line, err=True)


# --- Demo instance (docs/DEMO.md) ---------------------------------------------
def _demo(work):
    """Run a bundle command, turning its refusals into one line."""
    from kosha.db import SessionLocal
    from kosha import storage
    from kosha.demo import BundleError

    with SessionLocal() as session:
        try:
            result = work(session)
        except (BundleError, *storage.ERRORS) as exc:  # a refusal, a missing bundle, or the media storage
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from None
        session.commit()
    return result


@app.command("demo-export")
def demo_export(from_user: str = typer.Option(..., "--from-user", help="Email of the account whose library the demo is built from"),
                tags: str = typer.Option(..., help='Comma list of tag paths; only items and tags at or beneath them leave, e.g. "AI/RAG,Robotics"'),
                limit: int = typer.Option(300, help="At most this many saved items, newest first"),
                source: list[str] = typer.Option(None, "--source", help="Only items of this source (x, linkedin, ...); repeat for several"),
                exclude_tags: str = typer.Option("", "--exclude-tags", help="Comma list of tag paths; an item carrying a tag at or "
                                                                             "beneath one of them, in any role, is left out"),
                spread: bool = typer.Option(False, "--spread", help="Take the items from the --tags in turn (newest first within "
                                                                    "each) instead of the newest overall"),
                out: Path = typer.Option(..., help="Folder to write the bundle to"),
                queries: Path = typer.Option(None, help="Text file, one suggested demo search per line: their vectors are "
                                                        "embedded now (Voyage, a fraction of a cent) so the demo can rank them by meaning")):
    """Write a demo seed bundle and print what is in it (items per tag, content
    type and author; media files and bytes). Read the summary before
    publishing: the bundle is public data."""
    from kosha.demo import export_bundle
    from kosha.search.embeddings import EmbeddingError

    try:
        paths = lambda text: [t.strip() for t in text.split(",") if t.strip()]  # noqa: E731
        summary = _demo(lambda session: export_bundle(session, from_user, paths(tags), limit, out, queries, sources=source or None,
                                                      exclude_tag_paths=paths(exclude_tags), spread=spread))
    except EmbeddingError as exc:
        typer.echo(f"Cannot embed the suggested searches: {exc}", err=True)
        raise typer.Exit(1) from None
    typer.echo(summary)
    typer.echo(f"\nWritten to {out}. Nothing is published until you copy it to the demo server.")


@app.command("demo-load")
def demo_load(path: Path):
    """Create the demo user and load a bundle into an empty database (KOSHA_DEMO=true only)."""
    from kosha.demo import load_bundle

    typer.echo("loaded " + " ".join(f"{k}={v}" for k, v in _demo(lambda session: load_bundle(session, path)).items()))


@app.command("demo-reset")
def demo_reset(path: Path):
    """Empty the demo's database and load the bundle again (for a nightly timer; KOSHA_DEMO=true only)."""
    from kosha.demo import reset

    typer.echo("reset " + " ".join(f"{k}={v}" for k, v in _demo(lambda session: reset(session, path)).items()))
