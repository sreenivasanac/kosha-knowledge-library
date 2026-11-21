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
uv run kosha reindex          # once after a migration that touches the search columns
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
| `KOSHA_COOKIE_SECURE` | Default `true` (cookie sent over HTTPS only). Set `false` for plain-http local use. |
| `KOSHA_DEV` | `true` serves `/api/docs` and `/api/openapi.json` without signing in. Default `false`. |
| `KOSHA_X_ACCESS_TOKEN`, `KOSHA_X_USER_ID` | Optional, for the official X API sync |
| `VOYAGE_API_KEY` | Embeddings for semantic search. Without it search is keyword-only. |

Ports, page sizes, CORS origins, the app name and the defaults of user settings are constants in
`kosha/config.py`.

## Commands

| Command | What it does |
|---|---|
| `kosha serve` | Run the API server |
| `kosha migrate` | `alembic upgrade head` |
| `kosha create-admin` | Create or update the admin user and API token. Safe to re-run. |
| `kosha ingest-dir <path>` | Ingest every capture envelope (`*.json`) in a folder, in file-name order. Idempotent. Prints the sortIndex save-time check. |
| `kosha media-fetch [--limit N]` | Download photos and previews (and videos if `store_videos` is on). Resumable. Only https, only the hosts in `MEDIA_ALLOWED_HOSTS`, with a size cap. |
| `kosha reindex` | Rewrite the search columns (`search_text`, `tsv`, `tsv_extra`) of every item. Ingest keeps them current; run this after changing weights. |
| `kosha summarize` | Write rule-built summaries for items that have none or whose text changed. |
| `kosha embed [--limit N]` | Embed saved items whose text is new or changed (Voyage, paid; recorded in `spend_ledger`). Resumable. |
| `kosha sync-x` | Run the official X API bookmark sync once. Does nothing unless the `x_api_sync_enabled` setting is on. |
| `kosha reparse` | Parse again the stored captures whose parsing failed. |

After new captures: `kosha summarize`, then `kosha embed`. `kosha serve` also starts the daily X sync
thread (`kosha/sync/scheduler.py`); it calls X only when the setting is on.

## Layout

| Path | Contents |
|---|---|
| `kosha/config.py` | Constants and environment settings |
| `kosha/models.py`, `alembic/` | Every table in DESIGN.md section 4, one migration |
| `kosha/parsers/` | X GraphQL (`x_graphql.py`), official X API (`x_api.py`), LinkedIn. All return one shape (`common.py`). |
| `kosha/ingest.py` | Store the envelope, parse, merge ("richer payload wins field by field") |
| `kosha/items_query.py` | The basic list filters as SQL conditions, the stable order, and paging |
| `kosha/api/` | REST routes: captures, items, tags, sidebar/stats/settings, search, lists, removals |
| `kosha/auth.py` | Session-cookie login and bearer tokens |
| `kosha/media.py` | Media download and `GET /media/{id}` |
| `kosha/sync/` | Official X API bookmark sync (`x_api.py`) and its daily in-process scheduler |
| `kosha/search/query.py` | The qualifier language: tokenizer, parser, filter AST, SQL conditions |
| `kosha/search/engine.py` | Keyword and semantic rankers, reciprocal rank fusion, snippets, snapshots |
| `kosha/search/embeddings.py` | Voyage client, item embedding job, query-vector cache |
| `kosha/search/index.py` | Maintains the text-search columns |
| `kosha/search/suggest.py` | Autocomplete |
| `kosha/summaries.py` | Rule-built summaries, and the empty slot for an LLM recipe |

## Auth

- Web app: `POST /api/auth/login` with `{email, password}` sets a session cookie; `POST /api/auth/logout`; `GET /api/auth/me`.
- Extension and MCP: `Authorization: Bearer <KOSHA_API_TOKEN>`.
- Every other route under `/api`, and `/media/{id}`, needs one of the two. `GET /api/health` answers 200 when the
  token or session is valid and 401 otherwise.
- **CSRF: a cookie-authenticated request that is not GET/HEAD/OPTIONS must send the header `X-Requested-With: kosha`**,
  or it gets 403. Requests with a bearer token are exempt, and so is `POST /api/auth/login`. The session cookie is
  `SameSite=Strict`.
- Login is limited to 5 failed attempts a minute per client address (then 429).
- Request bodies over 20 MB are refused with 413.
- `/api/docs` and `/api/openapi.json` need a signed-in user unless `KOSHA_DEV=true`.

## Search

The language is specified in `../docs/SEARCH_DESIGN.md` section B; what was measured and what differs from the design
is in `../docs/experiments/search_eval_2026-11-21.md`.

| Route | What it does |
|---|---|
| `GET /api/items?q=&mode=&sort=&snapshot_id=&page=&page_size=` | Search. `mode` is `hybrid` (default when `q` has words), `keyword`, `semantic` or `filter`. Rows carry `snippet` and `score`; the page carries `mode`, `query` (canonical), `snapshot_id`, `refreshed`, `notice`, `took_ms`. |
| `GET /api/search/suggest?q=&cursor=` | `{replace_range: [start, end], suggestions: [{insert, label, count, kind}]}` for the token at the caret |
| `POST /api/search/parse` `{q}` | `{ok, canonical, chips, has_text, error}`; always 200 |
| `GET /api/search/recent` | The last 10 searches |
| `GET/POST /api/saved-views`, `PATCH/DELETE /api/saved-views/{id}` | Saved views `{name, query, params}`; the query is checked and stored in canonical form |
| `GET/POST /api/lists`, `GET /api/lists/{id}` | Lists; the detail has the items in list order |
| `POST/DELETE /api/lists/{id}/items` `{item_ids}`, `PUT /api/lists/{id}/order` `{item_ids}` | Add, remove, reorder |
| `GET /api/removals/pending`, `POST /api/removals/result` | Archived items to remove at the source, and the outcome (`removed` and `already_gone` hard-delete the item) |

- A query that does not parse is a 422 with `{"detail": {"message", "position"}}`; `position` is a character index into `q`.
- A ranked search (hybrid, keyword or semantic with words, or `similar:`) is stored as a snapshot for 30 minutes from
  last use, 500 items at most. Send its `snapshot_id` with `page=2…` so pages do not shift. If the snapshot has
  expired the search runs again and the response has `refreshed: true` and a new id.
- `snippet` is HTML-escaped text whose only tags are `<mark>…</mark>`; it can be inserted as HTML.
- `notice` is `no_exact_matches` (no item has all the words; the rows are related by meaning) or
  `semantic_unavailable` (Voyage could not be reached; keyword results only).
- Without `q` and `sort` the route behaves as before. With `q`, the saved/archived scope comes from the query
  (`is:archived`, `include:archived`, `include:context`); other list filters sent alongside still apply.
- Items the source gave no save order for now sort by the time Kosha stored them, among the rest.

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
