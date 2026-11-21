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
| `TEST_DATABASE_URL` | Same, for `kosha_test`. The tests refuse a database whose name does not start with `kosha_test`. |
| `KOSHA_SECRET_KEY` | Signs the session cookie |
| `KOSHA_ADMIN_EMAIL`, `KOSHA_ADMIN_PASSWORD` | The single account, created by `kosha create-admin` |
| `KOSHA_API_TOKEN` | Bearer token for the extension and MCP, registered by `kosha create-admin` |
| `KOSHA_MEDIA_DIR` | Where media is stored. Default `../data/media` |
| `KOSHA_COOKIE_SECURE` | Default `true` (cookie sent over HTTPS only). Set `false` for plain-http local use. |
| `KOSHA_DEV` | `true` serves `/api/docs` and `/api/openapi.json` without signing in. Default `false`. |
| `KOSHA_X_ACCESS_TOKEN`, `KOSHA_X_USER_ID` | Optional, for the official X API sync |
| `VOYAGE_API_KEY` | Embeddings for semantic search. Without it search is keyword-only. |
| `TYPESAFE_API_KEY` | TypeSafe Jev, for the `jev` and `score_labels` pipeline stages and `POST /api/jev/check` |
| `OPENAI_API_KEY` | Ask, image descriptions and transcripts (`kosha/llm.py`, through LiteLLM). Without it those routes answer 503. |

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
| `kosha/models.py`, `alembic/` | Every table in DESIGN.md section 4 |
| `kosha/parsers/` | X GraphQL (`x_graphql.py`), official X API (`x_api.py`), LinkedIn. All return one shape (`common.py`). |
| `kosha/ingest.py` | Store the envelope, parse, merge ("richer payload wins field by field") |
| `kosha/items_query.py` | The basic list filters as SQL conditions, the stable order, and paging |
| `kosha/api/` | REST routes: captures, items, tags, sidebar/stats/settings, search, lists, removals, pipeline, Ask and media text (`ai.py`), tokens/spend/sync (`account.py`) |
| `kosha/auth.py` | Session-cookie login and bearer tokens |
| `kosha/media.py` | Media download and `GET /media/{id}` |
| `kosha/sync/` | Official X API bookmark sync (`x_api.py`) and its daily in-process scheduler |
| `kosha/search/query.py` | The qualifier language: tokenizer, parser, filter AST, SQL conditions |
| `kosha/search/engine.py` | Keyword and semantic rankers, reciprocal rank fusion, snippets, snapshots |
| `kosha/search/embeddings.py` | Voyage client, item embedding job, query-vector cache |
| `kosha/search/index.py` | Maintains the text-search columns |
| `kosha/search/suggest.py` | Autocomplete |
| `kosha/summaries.py` | Rule-built summaries (the default, from `docs/experiments/summaries_2026-11-21.md`) and the batched LLM recipe it was compared with |
| `kosha/llm.py` | Every LLM and speech-to-text call: LiteLLM, the monthly cap, the `spend_ledger` row |
| `kosha/ask.py` | Ask: the tool-use loop over read-only pipelines, its system prompt and limits |
| `kosha/media_text.py` | Transcripts and image descriptions: estimate, queue, worker, refresh of summary and search columns |
| `kosha/pipeline/` | The pipeline `query` engine: `parser.py` (stages, positions), `executor.py` (every stage, the response cap, the grammar text), `writes.py` (preview and confirm tokens, tag/role/state/list writes, undo snapshots) |
| `kosha/jev.py` | Jev through `constella.jev`: the text an item's question reads, the answer cache, estimates, budgets, ledger rows |
| `kosha/runs.py` | Label runs: a trial tag namespace per experiment, coverage and spend, compare, promote |
| `kosha/add_item.py` | Add a URL or a text as an item (source `web`), with a guarded page fetch |
| `kosha/mcp_server.py` | The MCP server: six tools at `/mcp` |

## Auth

- Web app: `POST /api/auth/login` with `{email, password}` sets a session cookie; `POST /api/auth/logout`; `GET /api/auth/me`.
- Extension and MCP: `Authorization: Bearer <KOSHA_API_TOKEN>`.
- Every other route under `/api`, and `/media/{id}`, needs one of the two. `GET /api/health` answers 200 when the
  token or session is valid and 401 otherwise.
- **CSRF: a cookie-authenticated request that is not GET/HEAD/OPTIONS must send the header `X-Requested-With: kosha`**,
  or it gets 403. Requests with a bearer token are exempt, and so is `POST /api/auth/login`. The session cookie is
  `SameSite=Strict`.
- Login is limited to 5 failed attempts a minute per client address (then 429). `kosha serve` believes
  `X-Forwarded-For` from `127.0.0.1` only, so behind nginx on the same machine the address is the real client's.
  The limiter is in memory and per process; add one in nginx as well:

  ```nginx
  # http { }
  limit_req_zone $binary_remote_addr zone=kosha_login:10m rate=10r/m;
  # server { }
  location = /api/auth/login {
      limit_req zone=kosha_login burst=5 nodelay;
      proxy_set_header X-Forwarded-For $remote_addr;
      proxy_set_header X-Forwarded-Proto $scheme;
      proxy_pass http://127.0.0.1:8000;
  }
  ```

  Set the same two `proxy_set_header` lines on the main `location /` block.
- `GET/POST/DELETE /api/tokens` need the session cookie; a bearer token gets 403, so a token cannot mint or revoke tokens.
- A SQL statement run for a request (REST or MCP) is cancelled after 15 seconds (`STATEMENT_TIMEOUT_MS`); the CLI,
  migrations and background jobs use a second connection pool without the limit (`kosha/db.py`).
- `POST /api/removals/result` deletes only an item that is saved and archived (409 otherwise).
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
| `GET/POST /api/lists`, `GET/PATCH/DELETE /api/lists/{id}` | Lists; the detail has the items in list order. `PATCH` takes `name` and/or `description`; `DELETE` removes the list, not its items |
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

## Pipeline, Jev and MCP

Grammar, write rules, the MCP connect command and the agent playbook are in `../docs/MCP.md`; the numbers from the
real library are in `../docs/experiments/pipeline_smoke_2026-11-21.md`.

| Route | What it does |
|---|---|
| `POST /api/query` `{q, confirm?, run?}` | Run a pipeline, e.g. `where from:a,b \| grep ~retriev \| semantic "..." \| head 200 \| jev "...?" >= 0.8 \| tag "AI/RAG"`. Answers `{text, n, stages: [{stage, in, out, usd, ms, note}], rows, spent_usd, set, confirm, batch_id}`. A write over 25 items answers with a preview and `confirm`; send the same `q` again with it. Errors are 422 `{detail: {message, position}}`. |
| `POST /api/jev/check` `{q, mode, question, threshold, dry_run}` | `dry_run`: `{estimate_usd, n, cached}`, nothing spent. Otherwise `{snapshot_id, kept, n, spent_usd, cached, set}`; page through the kept items with `GET /api/items?q=…&snapshot_id=…` (each row's `score` is Jev's p). 402 when a budget would be passed. |
| `POST /api/undo/{batch_id}` | Also reverses a pipeline write (tags, roles, state, list). |
| `/mcp` | MCP over streamable HTTP, bearer token. Tools: `query`, `tags`, `edit_item`, `add_item`, `jobs`, `runs`. An agent cannot approve or reject tags; its changes to the owner's tags need a confirm token; every `tags` and `edit_item` change is an undo batch (`../docs/MCP.md`). |

- Jev reads the first 2,000 characters of an item's text, never the summary, and every call uses one request layout
  (many items per request); both follow Constella's measurements, quoted at the top of `kosha/jev.py`.
- Answers are cached in `jev_questions` / `jev_answers` by (question, item, text); a repeat costs nothing. Every call
  is a `spend_ledger` row with what it would have cost uncached.
- Budgets are the user settings `jev_run_budget_usd` (one call, inside a label run as well, and a run's total when it
  was started without a budget; default $0.50), `jev_run_max_budget_usd` (the most any label run may spend, whatever
  budget the agent asked for; default $2) and `jev_monthly_budget_usd` (default $5).
- Check and spend are one step: under a per-user Postgres advisory lock the estimate is checked and written to
  `spend_ledger` as a provisional row (`meta.provisional`), then corrected to the real cost after the call, or removed
  when the call failed and nothing was billed. The LLM layer does the same against `llm_monthly_cap_usd`, holding
  $0.05 for a chat completion (`COMPLETION_RESERVE_USD`) and the per-minute price for a transcription.
- `q` is at most 4,000 characters. The Jev spend token is bound to the pipeline, the number of new questions and the
  estimate; the promote token to the exact tags and (item, tag) pairs; `save view` onto an existing name with another
  filter answers with a preview and a confirm token.
- A label run writes to its own tag namespace (`run-<id>`). The app's lists, counts and `is:untagged` read `main`
  only, so a run never shows up there until it is promoted.
- Constella (`constella @ git+https://github.com/sreenivasanac/constella@library-v2`) supplies the Jev client,
  `group`, `shortlist`, `assign` and `tree_hints`.

## Ask, media text and account

Measured on the real library: `../docs/experiments/ask_eval_2026-11-21.md`, `summaries_2026-11-21.md`, `media_text_2026-11-21.md`.

| Route | What it does |
|---|---|
| `POST /api/ask` `{question, max_steps?}` | An LLM answers from the library through read-only pipelines. `{answer, items, steps: [{pipeline, n, note}], spent: {llm_usd, jev_usd}, took_ms, model, tokens}`. The answer cites items as `[id]`; `items` are those ids in the order cited. |
| `POST /api/ask/stream` | The same as server-sent events: `step` per pipeline, then `done` (the full answer) or `error`. |
| `POST /api/media-text/estimate` `{item_ids?, all?}` | `{n_media, minutes, images, unknown_duration, estimate_usd}` for the media of those items that has no text yet. `unknown_duration` recordings have no length from the source and are not in the estimate. |
| `POST /api/media-text/start` `{item_ids?, all?, confirm_usd?}` | 202: queues them and starts the worker thread. One item id is what the `m` key sends. With `all`, or an estimate over $0.50 (`MEDIA_TEXT_CONFIRM_ABOVE_USD`), `confirm_usd` must be at or above the current estimate; otherwise 409 `{detail: {message, n_media, minutes, images, unknown_duration, estimate_usd}}` and nothing is queued. 402 when the estimate would pass the LLM cap. |
| `GET /api/media-text/status` | `{counts: {none, queued, done, failed}, running, job, ffmpeg}`; `job.errors` gives the reason of each failure by media id. |
| `GET/POST /api/tokens`, `DELETE /api/tokens/{id}` | API tokens: name, created, last used. The secret is in the `POST` answer only (`token`). Session cookie only: 403 with a bearer token. |
| `GET /api/spend` | This month: `{month, total_usd, providers: [{provider, usd, calls}], caps: [{name, setting, cap_usd, spent_usd}]}` for `x`, `jev` and `llm`. |
| `POST /api/sync` `{source: "x_api"}`, `GET /api/sync` | Start the official X API sync in the background (`disabled` when the setting is off; nothing is called); the last run of each sync from `sync_state`. |

- Models are user settings, as LiteLLM names: `llm_ask_model` (default `openai/gpt-6.1-terra`), `llm_small_model` (`openai/gpt-6-luna`, image descriptions), `stt_model` (`openai/gpt-transcribe`). `llm_monthly_cap_usd` (default $5) stops every LLM and speech-to-text call for the month.
- Ask: at most 6 tool calls, each result cut to about 1,200 tokens, 40,000 input tokens over the loop, $0.02 of Jev a question. Write stages are refused on the server, and a set Ask saves (`as @name`, `groups`) is stored as `@ask.name`, so it cannot replace a set saved elsewhere. Bookmark text reaches the model inside `<library_data>` tags, which the system prompt declares untrusted.
- Media text: a video's smallest mp4 is downloaded to a temporary folder (https, `MEDIA_ALLOWED_HOSTS`, 200 MB cap), ffmpeg cuts the sound track into mp3 pieces, each is transcribed. Without ffmpeg the mp4 is sent as it is when it is under 25 MB, otherwise the file fails with that reason. A recording whose length the source did not give is measured with ffmpeg before the cap is checked; when that is not possible the file fails with that reason and nothing is sent. A video with no speech gets a description of its preview frame. Then the item's summary, content hash and search columns are refreshed.
- LinkedIn saved-posts captures: the parser reads the headline, line breaks, picture kind (photo, video cover, document cover, link preview, company logo) and link preview from the card, takes the post time from the activity id (the relative label is the fallback), and estimates the save time as a lower bound (`parse_saved_posts` in `kosha/parsers/linkedin.py`).

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
