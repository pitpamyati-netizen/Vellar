# uvloop is deliberately absent: see docs/adr/0004-no-uvloop.md
FROM python:3.14-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Dependency layer: cached until the lock file changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src/ ./src/
COPY content/ ./content/
COPY migrations/ ./migrations/
COPY scripts/healthcheck.py ./scripts/
# alembic.ini for the migration run; README.md because pyproject.toml names it as
# the package readme and hatchling refuses to build the project without it.
COPY alembic.ini README.md ./
RUN uv sync --frozen --no-dev

ENV PATH="/opt/venv/bin:${PATH}"

# Nothing the bot does needs root, and it never writes to its own source tree.
# The heartbeat goes to /tmp, which this user does own.
RUN useradd --create-home --uid 10001 vellar
USER vellar

# A process that dies is handled by the restart policy. A process whose event
# loop is wedged is only visible here - see src/mmorpg/health.py. The start
# period covers content validation and the first Telegram call.
HEALTHCHECK --interval=15s --timeout=10s --start-period=45s --retries=3 \
    CMD ["python", "scripts/healthcheck.py"]

# Explicit because the shutdown path depends on it: polling drains through
# aiogram's handler, the webhook through the stop event in mmorpg.main.
STOPSIGNAL SIGTERM

CMD ["python", "-m", "mmorpg.main"]
