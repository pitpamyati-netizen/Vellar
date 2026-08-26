# uvloop отсутствует нарочно: см. docs/adr/0004-no-uvloop.md
FROM python:3.14-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Слой зависимостей: кэшируется, пока не изменился файл блокировки. --locked, а не
# --frozen: зависимость, добавленная в pyproject.toml без пересборки блокировки, — это
# ошибка сборки здесь, а не ImportError на глазах у игроков.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY src/ ./src/
COPY content/ ./content/
COPY migrations/ ./migrations/
COPY scripts/healthcheck.py ./scripts/
# alembic.ini нужен для прогона миграций; README.md — потому что pyproject.toml называет
# его readme пакета, и без него hatchling отказывается собирать проект.
COPY alembic.ini README.md ./
RUN uv sync --locked --no-dev

ENV PATH="/opt/venv/bin:${PATH}"

# Ничему, что делает бот, root не нужен, и в собственное дерево исходников он не пишет
# никогда. Сердцебиение уходит в /tmp, а он этому пользователю принадлежит.
RUN useradd --create-home --uid 10001 vellar
USER vellar

# С умершим процессом разбирается политика перезапуска. Процесс, у которого встал цикл
# событий, виден только здесь — см. src/mmorpg/health.py. Начальный срок покрывает
# проверку содержимого и первый вызов Telegram.
HEALTHCHECK --interval=15s --timeout=10s --start-period=45s --retries=3 \
    CMD ["python", "scripts/healthcheck.py"]

# Указано явно, потому что от этого зависит путь остановки: опрос доигрывает через
# обработчик aiogram, вебхук — через событие остановки в mmorpg.main.
STOPSIGNAL SIGTERM

# Из какого рабочего дерева собран этот образ: штампует Start.bat, пишется в журнал на
# старте и печатается обратно через «Start.bat status». Последним слоем нарочно: новое
# значение пересобирает эту строку и больше ничего.
ARG VELLAR_BUILD="unknown"
ENV VELLAR_BUILD=${VELLAR_BUILD}
LABEL org.opencontainers.image.revision=${VELLAR_BUILD}

CMD ["python", "-m", "mmorpg.main"]
