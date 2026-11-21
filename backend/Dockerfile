# Kosha: one image with the API server and the built web app.
# Build from the repository root:  docker build -f backend/Dockerfile .
# (docker-compose.yml does this.)

# --- 1. Web app ---------------------------------------------------------------
FROM node:24-slim AS web
RUN npm install -g pnpm@12
WORKDIR /web
COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
RUN pnpm build

# --- 2. Python environment ----------------------------------------------------
# git is needed here only: the `constella` dependency is a git URL.
FROM python:3.12-slim AS backend
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.6 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
# The paths match the repository (backend/ next to web/dist): kosha/config.py
# finds alembic.ini and the web app relative to its own file.
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock backend/README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/alembic.ini ./
COPY backend/alembic ./alembic
COPY backend/kosha ./kosha
RUN uv sync --frozen --no-dev

# --- 3. Runtime ---------------------------------------------------------------
FROM python:3.12-slim
# ffmpeg: video transcripts (kosha/media_text.py).
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 1000 --create-home kosha \
    && mkdir -p /data/media && chown -R kosha:kosha /data
COPY --from=backend /app/backend /app/backend
COPY --from=web /web/dist /app/web/dist
ENV PATH="/app/backend/.venv/bin:$PATH" KOSHA_MEDIA_DIR=/data/media PYTHONUNBUFFERED=1
WORKDIR /app/backend
USER kosha
EXPOSE 8000
# Both setup steps are safe to repeat on every start.
CMD ["sh", "-c", "kosha migrate && kosha create-admin && exec kosha serve --host 0.0.0.0 --port 8000"]
