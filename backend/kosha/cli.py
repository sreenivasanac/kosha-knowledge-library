"""The `kosha` command line."""
from pathlib import Path

import typer
from sqlalchemy import select

from kosha.config import APP_NAME, BACKEND_DIR, SERVER_HOST, SERVER_PORT, get_settings

app = typer.Typer(help=f"{APP_NAME} backend", no_args_is_help=True, add_completion=False)


def _admin(session):
    """The single admin user, or exit with a hint."""
    from kosha.models import User

    user = session.scalar(select(User).where(User.email == get_settings().kosha_admin_email))
    if user is None:
        typer.echo("No admin user yet. Run: kosha create-admin", err=True)
        raise typer.Exit(1)
    return user


@app.command()
def serve(host: str = SERVER_HOST, port: int = SERVER_PORT, reload: bool = False):
    """Run the API server."""
    import uvicorn

    uvicorn.run("kosha.app:app", host=host, port=port, reload=reload)


@app.command()
def migrate():
    """Bring the database schema up to date."""
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config(str(BACKEND_DIR / "alembic.ini")), "head")
    typer.echo("Database is up to date.")


@app.command("create-admin")
def create_admin():
    """Create or update the admin user and API token from the environment."""
    from kosha.auth import create_admin as create
    from kosha.db import SessionLocal

    with SessionLocal() as session:
        create(session)
        session.commit()
    typer.echo("Admin user and API token are set.")


@app.command("ingest-dir")
def ingest_dir(path: Path):
    """Ingest every capture envelope (*.json) in a folder. Idempotent."""
    from kosha.db import SessionLocal
    from kosha.ingest import ingest_dir as ingest
    from kosha.models import Item
    from kosha.parsers.x_graphql import epoch_report

    with SessionLocal() as session:
        user = _admin(session)
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
def media_fetch(limit: int = typer.Option(None, help="Stop after this many files")):
    """Download media the settings ask for (photos and previews by default)."""
    from kosha.db import SessionLocal
    from kosha.media import fetch_media

    with SessionLocal() as session:
        counts = fetch_media(session, _admin(session).id, limit=limit)
    typer.echo(" ".join(f"{k}={v}" for k, v in counts.items()))
