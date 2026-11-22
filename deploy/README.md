# Deploying Kosha on one server

Used for the instance at kosha.pragnyalabs.com (systemd + nginx, Postgres with pgvector in Docker).

1. Postgres: `docker run -d --name kosha-db --restart unless-stopped -e POSTGRES_USER=kosha -e POSTGRES_PASSWORD=... -e POSTGRES_DB=kosha -p 127.0.0.1:5545:5432 -v ~/apps/kosha/pgdata:/var/lib/postgresql/data pgvector/pgvector:pg17`
2. Code: from the repo, `git ls-files -z | rsync -az --from0 --files-from=- ./ server:apps/kosha/`, then build the web app locally (`cd web && pnpm build`) and `rsync -az web/dist/ server:apps/kosha/web/dist/`.
3. `.env` in `~/apps/kosha/` on the server (mode 600): the variables listed in `backend/README.md`, with `KOSHA_COOKIE_SECURE=true` and `KOSHA_DEV=false`.
4. `cd ~/apps/kosha/backend && uv sync --no-dev && uv run kosha migrate && uv run kosha create-admin`
5. `kosha.service` to `/etc/systemd/system/`, then `sudo systemctl daemon-reload && sudo systemctl enable --now kosha.service`. `nginx-kosha.conf` to `/etc/nginx/sites-available/` (get the certificate with certbot before enabling the 443 block), `sudo ln -s /etc/nginx/sites-available/nginx-kosha.conf /etc/nginx/sites-enabled/`, `sudo nginx -t && sudo systemctl reload nginx`.
