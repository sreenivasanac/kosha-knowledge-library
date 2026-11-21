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
uv run kosha create-admin     # optional: admin user + API token from the environment (else: the setup screen)
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
| `KOSHA_SECRET_KEY` | Signs the session cookie and encrypts the API keys an admin saves. Changing it signs everyone out and makes saved keys unreadable (save them again). |
| `KOSHA_ADMIN_EMAIL`, `KOSHA_ADMIN_PASSWORD` | Optional. `kosha create-admin` makes (or updates) this admin account, so the setup screen is skipped. Without them the first account is created in the web app. |
| `KOSHA_API_TOKEN` | Bearer token for the extension and MCP, registered for the admin by `kosha create-admin` |
| `KOSHA_MEDIA_DIR` | Where media is stored with the `local` storage backend. Default `../data/media` |
| `KOSHA_STORAGE_BACKEND`, `KOSHA_S3_*` | Optional: media in an S3-compatible bucket; see "Media storage" |
| `DEEPINFRA_API_KEY` | Optional: speech to text with Whisper large-v3-turbo on DeepInfra instead of OpenAI; see "Ask, media text and account" |
| `KOSHA_COOKIE_SECURE` | Default `true` (cookie sent over HTTPS only). Set `false` for plain-http local use. |
| `KOSHA_DEV` | `true` serves `/api/docs` and `/api/openapi.json` without signing in. Default `false`. |
| `KOSHA_X_ACCESS_TOKEN`, `KOSHA_X_USER_ID` | Optional, for the official X API sync |
| `VOYAGE_API_KEY` | Embeddings for semantic search. Without it search is keyword-only. |
| `TYPESAFE_API_KEY` | TypeSafe Jev, for the `jev` and `score_labels` pipeline stages and `POST /api/jev/check` |
| `OPENAI_API_KEY` | Ask, image descriptions and transcripts (`kosha/llm.py`, through LiteLLM). Without it those routes answer 503. |
| `KOSHA_TRANSCRIPTION_API_KEY`, `KOSHA_TRANSCRIPTION_PROVIDER`, `KOSHA_TRANSCRIPTION_MODEL` | Optional: a separate speech-to-text provider's key, its name and model |
| `KOSHA_LANDING` | `true`: signed-out visitors get the landing page (`public-config.landing`). Default `false`; an admin can also switch it in the app. |
| `KOSHA_DEMO_URL` | Address of your public demo, linked from the landing page (`public-config.demo_url`). Default none. |
| `KOSHA_DEMO`, `KOSHA_DEMO_EMAIL`, `KOSHA_DEMO_PASSWORD` | Demo mode, for a separate instance with its own database: see "Demo mode" and `../docs/DEMO.md`. |

The four API keys can also be saved by an admin in the app (`/api/admin/keys`); a saved key wins over the
environment's. A variable that is present but empty (`OPENAI_API_KEY=`) counts as not set.

Ports, page sizes, CORS origins, the app name, the password rule, link lifetimes and the defaults of user
settings are constants in `kosha/config.py`.

## Commands

| Command | What it does |
|---|---|
| `kosha serve` | Run the API server |
| `kosha migrate` | `alembic upgrade head` |
| `kosha create-admin` | Create or update the admin user and API token from the environment. Safe to re-run; does nothing when `KOSHA_ADMIN_EMAIL` / `_PASSWORD` are not set. |
| `kosha ingest-dir <path>` | Ingest every capture envelope (`*.json`) in a folder, in file-name order. Idempotent. Prints the sortIndex save-time check. |
| `kosha media-fetch [--limit N]` | Download photos and previews (and videos if `store_videos` is on). Resumable. Only https, only the hosts in `MEDIA_ALLOWED_HOSTS`, with a size cap. |
| `kosha reindex` | Rewrite the search columns (`search_text`, `tsv`, `tsv_extra`) of every item. Ingest keeps them current; run this after changing weights. |
| `kosha summarize` | Write rule-built summaries for items that have none or whose text changed. |
| `kosha embed [--limit N]` | Embed saved items whose text is new or changed (Voyage, paid; recorded in `spend_ledger`). Resumable. |
| `kosha sync-x` | Run the official X API bookmark sync once. Does nothing unless the `x_api_sync_enabled` setting is on. |
| `kosha reparse` | Parse again the stored captures whose parsing failed. |
| `kosha storage-test` | Write, read, range-read and delete one small object on the active media storage. |
| `kosha storage-migrate --to local\|s3` | Copy every media file to the other storage backend, check the sizes, then make it the active one. The old copy stays. |
| `kosha import <file> [--user email] [--dry-run]` | Import a bookmarks file (browser, Pocket or Raindrop HTML; CSV with a `url` column). `--dry-run` only counts. |
| `kosha demo-export --from-user <email> --tags "<paths>" [--exclude-tags "<paths>"] [--source x]... [--spread] [--limit 300] --out <dir> [--queries <file>]` | Write a demo seed bundle from one library and print what is in it (`../docs/DEMO.md`). `--exclude-tags` leaves out any item carrying one of those tags (or a tag beneath) in any role; `--spread` takes items from the `--tags` in turn. Archived items and items without text never leave. |
| `kosha demo-load <dir>`, `kosha demo-reset <dir>` | Load a bundle into an empty demo database; empty a demo's database and load it again. Both refuse unless `KOSHA_DEMO=true`. |

`ingest-dir`, `media-fetch`, `summarize`, `embed` and `sync-x` work on one account: the admin, or `--user <email>`.

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
| `kosha/auth.py` | Accounts: setup, sign-up, session-cookie login, bearer tokens, password change and reset links |
| `kosha/api/admin.py` | Admin routes: users, invites, reset links, instance settings, API keys |
| `kosha/keys.py` | The one accessor for API keys (`get_key`): saved by an admin (encrypted) or from the environment |
| `kosha/prefs.py` | User settings, instance settings, and the instance's maximum for each spending cap |
| `kosha/demo.py` | Demo mode: what a demo refuses, and the seed bundle (export, load, reset) |
| `kosha/media.py` | Media download and `GET /media/{id}` |
| `kosha/storage.py`, `kosha/api/storage_admin.py` | Where media files live (a folder or an S3-compatible bucket), `Range` serving, and the admin routes: configuration, self-test, migration |
| `kosha/importers/`, `kosha/api/portability.py` | Import of bookmark files (Netscape HTML, Raindrop CSV, CSV) and export (CSV, HTML, JSON) |
| `kosha/sync/` | Official X API bookmark sync (`x_api.py`) and its daily in-process scheduler |
| `kosha/search/query.py` | The qualifier language: tokenizer, parser, filter AST, SQL conditions |
| `kosha/search/engine.py` | Keyword and semantic rankers, reciprocal rank fusion, snippets, snapshots |
| `kosha/search/embeddings.py` | Voyage client, item embedding job, query-vector cache |
| `kosha/search/index.py` | Maintains the text-search columns |
| `kosha/search/suggest.py` | Autocomplete |
| `kosha/summaries.py` | Rule-built summaries (the default, from `docs/experiments/summaries_2026-11-21.md`) and the batched LLM recipe it was compared with |
| `kosha/llm.py` | Every LLM and speech-to-text call: LiteLLM, the choice of speech-to-text provider, the refusal when no key is set, the monthly cap, the `spend_ledger` row |
| `kosha/media_text_eval.py` | `uv run python -m kosha.media_text_eval --n 20`: the two speech-to-text providers on the same library clips (agreement, cost, latency) |
| `kosha/ask.py` | Ask: the tool-use loop over read-only pipelines, its system prompt and limits |
| `kosha/media_text.py` | Transcripts and image descriptions: estimate, queue, worker, refresh of summary and search columns |
| `kosha/pipeline/` | The pipeline `query` engine: `parser.py` (stages, positions), `executor.py` (every stage, the response cap, the grammar text), `writes.py` (preview and confirm tokens, tag/role/state/list writes, undo snapshots) |
| `kosha/jev.py` | Jev through `constella.jev`: the text an item's question reads, the answer cache, estimates, budgets, ledger rows |
| `kosha/runs.py` | Label runs: a trial tag namespace per experiment, coverage and spend, compare, promote |
| `kosha/add_item.py` | Add a URL or a text as an item (source `web`), with a guarded page fetch |
| `kosha/mcp_server.py` | The MCP server: six tools at `/mcp` |

## Accounts

Each user has their own library, tags, lists, views, tokens, settings and spend; nothing is shared between users.
Email and password only. There is no mail server: a forgotten password is reset through a link an admin makes.

| Route | What it does |
|---|---|
| `GET /api/public-config` (no sign-in) | `{app_name, version, has_users, signups: "open" \| "invite" \| "closed", landing, demo: null \| {email, password, notice}, demo_url, links: {github, docs}}`. `has_users: false` means: show the setup screen. |
| `POST /api/setup` `{email, password, name}` | First run only: creates the admin and signs in. 409 once any account exists (of two calls at the same moment, one wins). |
| `POST /api/auth/signup` `{email, password, name, invite?}` | Creates a normal user and signs in when the instance allows it: `open` takes anyone, `invite` needs an unused invite token, `closed` answers 403. |
| `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me` | `me` and `login` answer `{id, email, role: "admin" \| "user", name, demo, onboarding_completed}`. A disabled account gets 403 at login. |
| `POST /api/auth/password` `{current, new}` | Change your password (session cookie only). Your other sessions end. |
| `POST /api/auth/reset` `{token, password}` | Set a new password with the token of an admin's reset link. Works once. |
| `GET /api/onboarding`, `PATCH /api/onboarding` `{step, state}`, `POST /api/onboarding/complete` | `{completed, steps: {account, sources, ai, media, look}}`; a step is `done`, `skipped` or `todo`. `ai` and `media` set up the instance, so a normal user sees them as `not_applicable`. |

- Passwords have at least 10 characters (`PASSWORD_MIN_CHARS`), checked on the server.
- Sign-ups are `closed` after setup. An admin opens them (`PATCH /api/admin/instance`) or makes invites; making an
  invite while sign-ups are closed switches the mode to `invite`.
- Wrong passwords, invites and reset tokens share the limiter described under Auth.

### Admin

Every `/api/admin/*` route needs an admin's session cookie. A bearer token gets 403, even the admin's own.

| Route | What it does |
|---|---|
| `GET /api/admin/users` | `[{id, email, name, role, disabled, created_at, last_login_at, item_count, storage_bytes}]` (`storage_bytes`: stored original files) |
| `POST /api/admin/users` `{email, password, name, role?}` | Create an account whatever the sign-up mode |
| `PATCH /api/admin/users/{id}` `{role?, disabled?, name?}` | A disabled user's sessions and tokens stop working at once. The last active admin cannot be demoted or disabled (409). |
| `DELETE /api/admin/users/{id}` | Deletes the user with their library and stored files. Not yourself (409), so an admin always remains. |
| `POST /api/admin/users/{id}/reset-link` | `{url, expires_at}`: a single-use link (24 hours) to `/reset?token=…` that you send to the user |
| `POST /api/admin/invites` `{expires_days?}` | `{id, url, token, expires_at}`: a single-use link to `/signup?invite=…`; the token is shown this once |
| `GET /api/admin/invites`, `DELETE /api/admin/invites/{id}`, `DELETE /api/admin/invites` | `[{id, created_at, expires_at, used_at, used_by, state: "open" \| "used" \| "expired"}]`; revoke one; revoke every unused one |
| `GET/PATCH /api/admin/instance` | `{signups, landing, caps}`. `caps` is the most a normal user may set each spending cap to (`jev_monthly_budget_usd`, `llm_monthly_cap_usd`, `voyage_monthly_cap_usd`, `x_api_monthly_cap_usd`, `jev_run_budget_usd`, `jev_run_max_budget_usd`). |
| `GET /api/admin/keys` | `[{name, set, source: "env" \| "db" \| null, last4}]` for `voyage`, `typesafe`, `openai`, `transcription` (which also has `provider`, `model`). A key is never returned. |
| `PUT /api/admin/keys` `{name, value, provider?, model?}` | Save a key, encrypted with a key derived from `KOSHA_SECRET_KEY`. It wins over the environment's. |
| `DELETE /api/admin/keys?name=…` (or `/api/admin/keys/{name}`) | Forget the saved key; the environment's applies again |
| `POST /api/admin/keys/test` `{name}` | `{ok, message}` after one tiny call (OpenAI: the model list, free; Voyage: one word; TypeSafe: one question) |

- Spend and caps are per user. Paid calls run on the instance's keys, so a normal user's caps are held at or under
  the instance's `caps` whatever they set; an admin's own caps are theirs to set. The defaults let each user spend
  what a default install could ($5 a month each on Jev, LLM calls and Voyage).
- The server's X API credentials are one X account, the admin's: the official X sync runs for admins only.
  Other users capture with the extension.
- Code that needs a key calls `kosha.keys.get_key("voyage" | "typesafe" | "openai" | "transcription")`.

### Demo mode

`KOSHA_DEMO=true` (with `KOSHA_DEMO_EMAIL`, `KOSHA_DEMO_PASSWORD`) turns an instance into a public demo: a
**separate** instance with its own database, loaded from a seed bundle and reset every night (`../docs/DEMO.md`).
`public-config.demo` publishes the credentials. Visitors can tag, star, read, archive, write notes, make lists and
views, remap keys, run keyword search and free pipelines. The server refuses the rest with
403 `{"detail": {"message": "Not available in the demo", "code": "demo"}}`:

- setup, sign-up, password change and reset, everything under `/api/admin`
- creating and deleting API tokens, bearer-token auth and `/mcp`
- captures, imports, adding items, media-text start, Ask, the Jev check, sync, removals, permanent delete
- the pipeline stages `jev`, `score_labels` and `rerank`, and `rerank=voyage|jev` on a search
- any other changing request that is not on the allow-list in `kosha/demo.py` (a new route is closed in the demo
  until it is added there)

A demo never calls the embedding API. A search by meaning uses the stored vector of one of the bundle's suggested
searches (`demo_query_vectors`); for any other text it falls back to keywords and the page carries
`notice: "demo_keyword_only"`. Changing requests are limited to 60 a minute per client address (then 429).

## Auth

- Web app: `POST /api/auth/login` with `{email, password}` sets a session cookie; `POST /api/auth/logout`; `GET /api/auth/me`.
- Extension and MCP: `Authorization: Bearer <token>`: `KOSHA_API_TOKEN` (the admin's) or a token a user makes in Settings. A token acts as its own user.
- Every other route under `/api`, and `/media/{id}`, needs one of the two. `GET /api/health` answers 200 when the
  token or session is valid and 401 otherwise.
- **CSRF: a cookie-authenticated request that is not GET/HEAD/OPTIONS must send the header `X-Requested-With: kosha`**,
  or it gets 403. Requests with a bearer token are exempt, and so is `POST /api/auth/login`. The session cookie is
  `SameSite=Strict`.
- Login is limited to 5 failed attempts a minute per client address (then 429); wrong invites, reset tokens and current passwords count too. `kosha serve` believes
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
- `GET/POST/DELETE /api/tokens`, `POST /api/auth/password` and `/api/admin/*` need the session cookie; a bearer token gets 403, so a token cannot mint or revoke tokens, change the password or administer the instance.
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
- A `jev` or `score_labels` stage on a server without a TypeSafe key answers 503 `{detail: {message, position}}`.
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
| `POST /api/ask/stream` | The same as server-sent events: `step` per pipeline, then `done` (the full answer) or `error` with `{status, message}` (402 when the monthly LLM cap is used up). |
| `POST /api/media-text/estimate` `{item_ids?, all?}` | `{n_media, minutes, expected_billed_minutes, images, unknown_duration, estimate_usd, provider, model}` for the media of those items that has no text yet. `minutes` is all the recorded time; `expected_billed_minutes` leaves out the share the speech pre-filter usually skips (10%), and is what `estimate_usd` prices at `provider`'s rate for `model`. `unknown_duration` recordings have no length from the source and are not in the estimate. |
| `POST /api/media-text/start` `{item_ids?, all?, confirm_usd?}` | 202: queues them and starts the worker thread. One item id is what the `m` key sends. With `all`, or an estimate over $0.50 (`MEDIA_TEXT_CONFIRM_ABOVE_USD`), `confirm_usd` must be at or above the current estimate; otherwise 409 `{detail: {message, …the estimate's fields}}` and nothing is queued. 402 when the estimate would pass the LLM cap. |
| `GET /api/media-text/status` | `{counts: {none, queued, done, failed}, running, job, ffmpeg}`; `job.errors` gives the reason of each failure by media id. |
| `GET/POST /api/tokens`, `DELETE /api/tokens/{id}` | API tokens: name, created, last used. The secret is in the `POST` answer only (`token`). Session cookie only: 403 with a bearer token. |
| `GET /api/spend` | This month: `{month, total_usd, providers: [{provider, usd, capped_usd, calls}], caps: [{name, setting, cap_usd, spent_usd, counts}], uncapped: [{provider, operation, usd, calls}], note}` for `x`, `jev`, `llm` and `voyage`, for the signed-in user. The `llm` cap counts the app's own LLM and speech-to-text calls (`meta.via = llm`); calls made by scripts are under no cap and listed in `uncapped`. |
| `POST /api/sync` `{source: "x_api"}`, `GET /api/sync` | Start the official X API sync in the background (`{status: "disabled" \| "not_configured", reason}` when the setting is off or the server has no X API credentials; nothing is called); the last run of each sync source from `sync_state`. |

- Models are user settings, as LiteLLM names: `llm_ask_model` (default `openai/gpt-6.1-terra`), `llm_small_model` (`openai/gpt-6-luna`, image descriptions), `stt_model` (speech to text; see the next point). `llm_monthly_cap_usd` (default $5) stops every LLM and speech-to-text call for the month.
- Speech to text goes through `litellm.transcription`, so any provider LiteLLM supports can be used. The model is, in this order: the user's saved `stt_model` setting; the instance's transcription model (admin settings, or `KOSHA_TRANSCRIPTION_PROVIDER` / `KOSHA_TRANSCRIPTION_MODEL`); `deepinfra/openai/whisper-large-v3-turbo` ($0.0002 a minute) when a DeepInfra key is set (`DEEPINFRA_API_KEY`, or the instance key `transcription`); else `openai/gpt-transcribe` ($0.0045 a minute). Measured on library clips in `../docs/experiments/transcription_2026-11-21.md`: the two agree on about 95% of words on clear English speech; for Tamil, OpenAI was clearly better.
- A call whose provider has no key (an empty key counts as none) answers 503 `no LLM key configured` before anything is sent or held against the cap.
- Ask: at most 6 tool calls, each result cut to about 1,200 tokens, 40,000 input tokens over the loop, $0.02 of Jev a question. Write stages are refused on the server, and a set Ask saves (`as @name`, `groups`) is stored as `@ask.name`, so it cannot replace a set saved elsewhere. Bookmark text reaches the model inside `<library_data>` tags, which the system prompt declares untrusted.
- Media text: a video's smallest mp4 is downloaded to a temporary folder (https, `MEDIA_ALLOWED_HOSTS`, 200 MB cap), ffmpeg cuts the sound track into mp3 pieces, each is transcribed. Without ffmpeg the mp4 is sent as it is when it is under 25 MB, otherwise the file fails with that reason. A recording whose length the source did not give is measured with ffmpeg before the cap is checked; when that is not possible the file fails with that reason and nothing is sent. Before the paid call, Silero VAD (a 1.3 MB ONNX model run locally with `onnxruntime`; no torch) listens to the sound track: a recording with under 3 seconds of speech (`MIN_SPEECH_S`) is not sent, and if the filter itself fails the recording is sent as before. A video with no sound track, a silent one, or one with no speech gets a description of its preview frame instead (its `extract_kind` is `description`; the job state counts them as `no_speech`). On 60 library videos, 62% of the files and 10% of the minutes were not sent. Then the item's summary, content hash and search columns are refreshed.
- LinkedIn saved-posts captures: the parser reads the headline, line breaks, picture kind (photo, video cover, document cover, link preview, company logo) and link preview from the card, takes the post time from the activity id (the relative label is the fallback), and estimates the save time as a lower bound (`parse_saved_posts` in `kosha/parsers/linkedin.py`).

## Media storage

`item_media.local_path` and `local_preview_path` hold a key (`<item id>/<media id>.jpg`) relative to the storage root, so the
same rows work on either backend. Every read, write and delete goes through `kosha/storage.py`.

| Backend | What it is |
|---|---|
| `local` (default) | A folder: `KOSHA_MEDIA_DIR`, or `local_dir` in the admin settings. A NAS or rclone mount is a folder too. |
| `s3` | Any S3-compatible bucket through one boto3 driver: AWS S3, Cloudflare R2, Backblaze B2, Hetzner Object Storage, Garage, MinIO, SeaweedFS. Tested against SeaweedFS in Docker. |

| S3 setting | Environment | Notes |
|---|---|---|
| `endpoint` | `KOSHA_S3_ENDPOINT` | Leave empty for AWS |
| `region` | `KOSHA_S3_REGION` | `auto` (the default) for R2 |
| `bucket` | `KOSHA_S3_BUCKET` | |
| `access_key_id`, `secret_access_key` | `KOSHA_S3_ACCESS_KEY_ID`, `KOSHA_S3_SECRET_ACCESS_KEY` | Saved through the admin route, the secret is encrypted like the API keys and never returned |
| `path_style` | `KOSHA_S3_PATH_STYLE` | `true` for Garage, MinIO and SeaweedFS |
| `prefix` | `KOSHA_S3_PREFIX` | A folder inside the bucket |
| `serve_mode` | `KOSHA_S3_SERVE_MODE` | `proxy` (default): the app streams the object and answers `Range` requests, so video seeking works and the bucket stays private. `presigned`: `GET /media/{id}` redirects to an address signed for 5 minutes (`PRESIGN_TTL_S`). |
| `public_base_url` | `KOSHA_S3_PUBLIC_BASE_URL` | With `presigned`: redirect to this public address of the bucket instead of signing |

The environment gives the starting values; what an admin saves (the `storage` row of `instance_settings`) wins.

| Route (admin session cookie) | What it does |
|---|---|
| `GET /api/admin/storage` | `{backend, local_dir, s3: {endpoint, region, bucket, prefix, path_style, serve_mode, public_base_url, key_set}, usage: {files, bytes}}` |
| `PATCH /api/admin/storage` | Any part of the same shape, plus `s3.access_key_id` and `s3.secret_access_key`. Setting `backend` switches at once and copies nothing; a backend that cannot be built is refused (422). |
| `POST /api/admin/storage/test` `{backend?}` | Writes, reads, range-reads and deletes one small object: `{ok, message}`. With `backend: "s3"` it tries the S3 settings while `local` is still active. |
| `POST /api/admin/storage/migrate` `{to}` | 202: copies every object to the other backend in the background, checks each size, and makes `to` the active backend only when everything arrived. The source is never deleted. |
| `GET /api/admin/storage/migrate` | `{running, from, to, total, copied, bytes, failed, errors, switched, message, started_at, finished_at}` |

- A migration can be run again after a failure: objects already there at the right size are skipped. Progress is kept in
  the server's memory, so run it with one server process.
- The S3 tests need a server: `docker run -d --name kosha-s3-test -p 127.0.0.1:19123:8333 -v "$PWD/s3.json:/etc/s3.json:ro" chrislusf/seaweedfs server -s3 -s3.config=/etc/s3.json`
  (with an identity in `s3.json`), then run pytest with `KOSHA_TEST_S3_ENDPOINT=http://127.0.0.1:19123`,
  `KOSHA_TEST_S3_ACCESS_KEY_ID` and `KOSHA_TEST_S3_SECRET` set. Without them those tests are skipped.

## Import and export

| Route | What it does |
|---|---|
| `POST /api/import` (multipart `file`; `format?`, `dry_run?` as query or form fields) | Imports a bookmarks file, 20 MB at most: `{format, created, duplicates, skipped, errors}`; `errors` explains up to 20 skipped rows. With `dry_run=true` nothing is written and the first 10 parsed rows come back as `rows`. |
| `GET /api/export?format=csv\|html\|json&q=` | Streams the saved items `q` matches (the search qualifiers; everything saved and not archived without it). |

| Import format | Read as |
|---|---|
| `netscape` | The bookmarks HTML of browsers, Raindrop and Pocket. Folders become collections (`Reading/Papers` for a nested one), `TAGS="a,b/c"` become tags (`b/c` is tag `c` under `b`), `ADD_DATE` the save time, `<DD>` the body. |
| `raindrop_csv` | Columns `url, folder, title, note, tags, created`, and `excerpt` (body), `cover` (a photo), `favorite` (star) |
| `csv` | Any CSV with a `url` column; the same optional columns, header names in any case |

- The format is found from the content unless `format` names it.
- An item is source `web`, with the id `add_item` gives the same URL; an X or LinkedIn post URL becomes source `x` / `linkedin`
  with that post's own id, so the import and a capture of the post are one item.
- A URL already in the library, or twice in the file, is a duplicate and is left as it is. Rows without an http(s) address,
  and items that were deleted for good, are skipped.
- Items go through `upsert_result`, so the search columns and rule-built summary are written. **No URL is fetched.**
- Export: `csv` has Raindrop's columns (`id, title, note, excerpt, url, folder, tags, created, cover, highlights, favorite`);
  `html` is a Netscape file with one `<H3>` per folder and full tag paths in `TAGS`; `json` is `{"items": [...]}` with every
  field of the item detail (tags, media with extracts, links, collections), without raw payloads or embeddings.

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
