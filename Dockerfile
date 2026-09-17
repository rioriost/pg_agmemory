FROM python:3.12.14-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254 AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"
WORKDIR /app

FROM base AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.15@sha256:62f8c047d0a0e9ece6b53fc63df902585a67a47a7f318ddec4a37db586edc8e3 /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src/ ./src/

FROM build AS test
RUN uv sync --frozen --extra dev --extra mcp --extra hook \
    && python -m compileall -q .venv/lib/python3.12/site-packages/janome
COPY tests/ ./tests/
CMD ["sh", "-c", "ruff check . && mypy && pytest"]

FROM build AS runtime-deps
RUN uv sync --frozen --no-dev --no-editable --extra mcp --extra hook \
    && python -m compileall -q .venv/lib/python3.12/site-packages/janome

FROM build AS adapter-extras-check
COPY scripts/check-adapter-extras.py ./check-adapter-extras.py
RUN uv sync --frozen --no-dev --no-editable \
    && python check-adapter-extras.py core \
    && uv sync --frozen --no-dev --no-editable --extra hook \
    && python check-adapter-extras.py hook

FROM base AS runtime
RUN groupadd --system --gid 10001 pgag \
    && useradd --system --uid 10001 --gid pgag --no-create-home --home-dir /app pgag
COPY --from=runtime-deps /app/.venv /app/.venv
USER 10001:10001
EXPOSE 8000
CMD ["pg-agmemory", "serve"]
