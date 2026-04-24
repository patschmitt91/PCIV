# syntax=docker/dockerfile:1.7

# ---- builder ----
FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never

COPY --from=ghcr.io/astral-sh/uv:0.4 /uv /usr/local/bin/uv

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY plan.yaml ./plan.yaml

RUN uv sync --no-dev --frozen

# ---- runtime ----
FROM python:3.12-slim AS runtime

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LOG_FORMAT=json

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 1001 pciv \
    && useradd --system --uid 1001 --gid 1001 --home-dir /app --shell /usr/sbin/nologin pciv

WORKDIR /app

COPY --from=builder --chown=pciv:pciv /app /app

USER pciv

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD ["pciv", "doctor"]

ENTRYPOINT ["pciv"]
CMD ["--help"]
