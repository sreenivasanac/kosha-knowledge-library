# Kosha backend (API server and worker share this image).
# NOT TESTED: written on a machine without Docker.
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.6 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH"
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY alembic.ini ./
COPY alembic ./alembic
COPY kosha ./kosha
RUN uv sync --frozen --no-dev
EXPOSE 8000
CMD ["sh", "-c", "kosha migrate && kosha create-admin && kosha serve --host 0.0.0.0 --port 8000"]
