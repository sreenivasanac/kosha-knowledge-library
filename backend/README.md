# Kosha backend

FastAPI + SQLAlchemy 2 + Postgres (pgvector, pg_trgm, ltree). It receives raw captures from the
browser extension, parses them on the server into items, and serves the REST API the web app uses.
The design is in `../DESIGN.md` (sections 3 to 5) and `../docs/CAPTURE_CONTRACT.md`.

## Run

Needs Python 3.12 through `uv`, and Postgres 17 with the three extensions available.

```sh
cd backend
uv sync
uv run kosha migrate          # create / update the schema
uv run kosha create-admin     # admin user + API token, from the environment
uv run kosha serve            # http://127.0.0.1:8000  (--host, --port, --reload)
uv run pytest                 # tests, against the kosha_test database only
```

API docs: `http://127.0.0.1:8000/api/docs`, schema at `/api/openapi.json`.

## Environment

Read from the repo-root `.env` (never committed, never logged).

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | `postgresql+psycopg://user@host:port/kosha` |
| `TEST_DATABASE_URL` | Same, for `kosha_test`. The tests refuse any other database name. |
| `KOSHA_SECRET_KEY` | Signs the session cookie |
| `KOSHA_ADMIN_EMAIL`, `KOSHA_ADMIN_PASSWORD` | The single account, created by `kosha create-admin` |
| `KOSHA_API_TOKEN` | Bearer token for the extension and MCP, registered by `kosha create-admin` |
| `KOSHA_MEDIA_DIR` | Where media is stored. Default `../data/media` |
| `KOSHA_COOKIE_SECURE` | `true` behind HTTPS |
| `KOSHA_X_ACCESS_TOKEN`, `KOSHA_X_USER_ID` | Optional, for the official X API sync |

Ports, page sizes, CORS origins, the app name and the defaults of user settings are constants in
`kosha/config.py`.

## Commands

| Command | What it does |
|---|---|
| `kosha serve` | Run the API server |
| `kosha migrate` | `alembic upgrade head` |
| `kosha create-admin` | Create or update the admin user and API token. Safe to re-run. |
| `kosha ingest-dir <path>` | Ingest every capture envelope (`*.json`) in a folder, in file-name order. Idempotent. Prints the sortIndex save-time check. |
| `kosha media-fetch [--limit N]` | Download photos and previews (and videos if `store_videos` is on). Resumable. |

## Layout

| Path | Contents |
|---|---|
| `kosha/config.py` | Constants and environment settings |
| `kosha/models.py`, `alembic/` | Every table in DESIGN.md section 4, one migration |
| `kosha/parsers/` | X GraphQL (`x_graphql.py`), official X API (`x_api.py`), LinkedIn. All return one shape (`common.py`). |
| `kosha/ingest.py` | Store the envelope, parse, merge ("richer payload wins field by field") |
| `kosha/items_query.py` | Filters to SQL conditions, and paging. The search filter AST plugs in here. |
| `kosha/api/` | REST routes: captures, items, tags, sidebar/stats/settings |
| `kosha/auth.py` | Session-cookie login and bearer tokens |
| `kosha/media.py` | Media download and `GET /media/{id}` |
| `kosha/sync/x_api.py` | Official X API bookmark sync (function only; nothing schedules it yet) |
| `kosha/search/` | Placeholder for the search work |

## Auth

- Web app: `POST /api/auth/login` with `{email, password}` sets a session cookie; `POST /api/auth/logout`; `GET /api/auth/me`.
- Extension and MCP: `Authorization: Bearer <KOSHA_API_TOKEN>`.
- Every other route under `/api`, and `/media/{id}`, needs one of the two.

## Captures

`POST /api/captures` takes the envelope in `docs/CAPTURE_CONTRACT.md` and answers
`{capture_id, items_new, items_updated, items_unchanged}`. Counts cover saved items; quoted posts
and reply parents are stored as context items (`is_saved = false`) and are not counted. An envelope
whose parsing fails is still stored, with `captures.parse_error` set.

## Docker

`Dockerfile` here and `../docker-compose.yml` (app, worker, Postgres with pgvector) are written but
**not tested**: the machine they were written on has no Docker.

## Credits

The X GraphQL extraction logic is ported from
[twitter-web-exporter](https://github.com/prinsss/twitter-web-exporter) (MIT). See `NOTICE`.
