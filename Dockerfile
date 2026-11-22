# Fleet Copilot: the web app, the API and the evaluation suite in one image.
# The image mirrors the repository layout under /app, so every default path in the code still holds.

# ---- 1. Build the web app -------------------------------------------------------------------------------
FROM node:22-slim AS web
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- 2. The Python service ------------------------------------------------------------------------------
FROM python:3.14-slim AS app
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/backend/.venv \
    FLEET_CONTAINER=1 \
    FLEET_STATE_DIR=/app/var

WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/ ./
RUN uv sync --frozen --no-dev

WORKDIR /app
COPY data/ data/
COPY evals/ evals/
COPY contracts/ contracts/
COPY --from=web /app/frontend/dist frontend/dist
COPY docker/entrypoint.sh /usr/local/bin/fleet
RUN chmod 0755 /usr/local/bin/fleet \
    && useradd --create-home --uid 10001 fleet \
    && mkdir -p /app/var \
    && chown -R fleet:fleet /app/var /app/evals

USER fleet
EXPOSE 8000
VOLUME ["/app/var"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["/app/backend/.venv/bin/python", "-c", "import urllib.request,os; urllib.request.urlopen('http://127.0.0.1:%s/' % (os.environ.get('FLEET_PORT') or '8000'), timeout=4)"]

ENTRYPOINT ["fleet"]
CMD ["serve"]
