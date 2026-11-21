# Running a public demo

A demo is a **separate Kosha instance with its own database**, filled from a seed bundle that you export from a real
library and reset to that bundle every night. Visitors sign in with one published account and can do everything that
is free and reversible. It never shares a database, a media folder or a `.env` with your own instance.

What a demo allows and refuses is listed in `backend/README.md` ("Demo mode"); the rules are in `backend/kosha/demo.py`.

## 1. Build the bundle (on your own instance)

Choose the tags whose items may be public. Only saved, unarchived items that carry a tag at or beneath one of them
are exported, newest first, up to `--limit`.

```sh
cd backend
# One suggested search per line, about 40 of them. The demo can rank only these by meaning.
cat > /tmp/demo-queries.txt <<'EOF'
retrieval augmented generation
how to evaluate a search pipeline
robot arm inverse kinematics
EOF

uv run kosha demo-export --from-user you@example.com \
  --tags "AI/RAG,Robotics" --limit 300 \
  --out ~/kosha-demo-bundle --queries /tmp/demo-queries.txt
```

The command prints a summary (also saved as `SUMMARY.txt` in the bundle): totals, items per tag and items per author.
**Read it before you publish anything.** Open `bundle.json` if you want to check single items.

| In the bundle | Not in the bundle |
|---|---|
| The selected items: text, author, dates, counts, summary | Notes, stars, read state, archive state |
| The posts they quote or answer (context items) | Raw captured payloads, captures |
| Their media rows, and the stored media files (`media/`) | Jev answers, spend, settings, API keys, tokens |
| Their links and embeddings | Lists, saved views, result sets, search history |
| The tags inside the chosen subtrees, with the parents that lead to them | Every other tag, and tags that are not approved |
| The vectors of the suggested searches (`--queries`) | Source folders |

- `--queries` calls Voyage once for all lines (a fraction of a cent, recorded in your spend). Without it, or without
  a Voyage key, the bundle has no query vectors and the demo searches by keyword only.
- A quoted post that is itself one of your saved items but is not in the selection is left out; the item that quotes
  it shows the quote as missing.
- Exporting changes nothing in your library and publishes nothing. The bundle is a folder; you copy it yourself.

## 2. Set up the demo instance

On the demo server, with its own Postgres database (see `deploy/README.md` for Postgres, nginx and systemd):

```sh
# ~/apps/kosha-demo/.env  (mode 600)
DATABASE_URL=postgresql+psycopg://kosha@localhost:5546/kosha_demo
KOSHA_SECRET_KEY=<openssl rand -hex 32>          # not the key of your own instance
KOSHA_MEDIA_DIR=/home/agent/apps/kosha-demo/media
KOSHA_COOKIE_SECURE=true
KOSHA_DEMO=true
KOSHA_DEMO_EMAIL=demo@example.com
KOSHA_DEMO_PASSWORD=<any password; it is shown on the sign-in page>
# No VOYAGE_API_KEY, TYPESAFE_API_KEY or OPENAI_API_KEY, and no KOSHA_ADMIN_* or KOSHA_API_TOKEN.
```

```sh
rsync -az ~/kosha-demo-bundle/ server:apps/kosha-demo/bundle/
cd ~/apps/kosha-demo/backend
uv sync --no-dev
uv run kosha migrate
uv run kosha demo-load ../bundle        # creates the demo user and loads the bundle; needs an empty database
uv run kosha serve --port 8051
```

`demo-load` and `demo-reset` refuse to run unless `KOSHA_DEMO=true`, so neither can touch a normal instance by
mistake. Leave the API keys out of the demo's `.env`: the server refuses every paid action anyway, and a key that is
not there cannot be spent.

On your own instance, set `KOSHA_DEMO_URL=https://demo.example.com` (and `KOSHA_LANDING=true` if you want the
landing page) so the web app can link to the demo.

## 3. Reset every night

`kosha demo-reset <dir>` empties every table and loads the bundle again in one transaction, so a visitor sees the old
state or the new one, never half of each. The demo user's password hash is kept, so people who are signed in stay
signed in. If another transaction holds the tables for more than 30 seconds the reset gives up and exits with an
error; the next run tries again.

```ini
# /etc/systemd/system/kosha-demo-reset.service
[Unit]
Description=Reset the Kosha demo to its seed bundle

[Service]
Type=oneshot
User=agent
WorkingDirectory=/home/agent/apps/kosha-demo/backend
EnvironmentFile=/home/agent/apps/kosha-demo/.env
ExecStart=/home/agent/.local/bin/uv run --no-sync kosha demo-reset /home/agent/apps/kosha-demo/bundle
```

```ini
# /etc/systemd/system/kosha-demo-reset.timer
[Unit]
Description=Nightly reset of the Kosha demo

[Timer]
OnCalendar=*-*-* 03:30:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now kosha-demo-reset.timer
systemctl list-timers kosha-demo-reset.timer     # when it runs next
journalctl -u kosha-demo-reset.service -n 20     # what the last run printed
```

To publish a new selection, export again, copy the bundle over the old one and run `kosha demo-reset` once.

## 4. Limits to know

- **Write limit.** The server allows 60 changing requests a minute per client address (`DEMO_WRITES_PER_MINUTE`),
  counted in memory per process. Add one in nginx too:

  ```nginx
  # http { }
  limit_req_zone $binary_remote_addr zone=kosha_demo:10m rate=60r/m;
  # server { } of the demo
  location /api/ {
      limit_req zone=kosha_demo burst=30 nodelay;
      proxy_set_header X-Forwarded-For $remote_addr;
      proxy_set_header X-Forwarded-Proto $scheme;
      proxy_pass http://127.0.0.1:8051;
  }
  ```

- **Search by meaning** works for the suggested searches only (matched without regard to case). Any other text is
  answered by keyword search, and the response says so (`notice: "demo_keyword_only"`). "More like this"
  (`similar:<id>`) and `groups` use the stored item embeddings and work for every item.
- **Everyone shares one account**, so visitors see each other's tags, notes and lists until the next reset. Do not
  put anything in the demo that should not be public, and expect visitors to write anything into notes and tag names.
- **Media files** are copied into `demo/` inside the demo's media storage. Visitors cannot delete items, so the
  files are never removed; a reset copies them again over the same names.
- A demo has no admin account. To change instance settings, change the `.env` and restart.
