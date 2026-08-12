# Claude.md — карта проекта и правила разработки

Vellar — текстовая MMORPG в Telegram для незрячих игроков. Python 3.14, aiogram 3,
PostgreSQL, Redis, гексагональная архитектура. Весь текст игрока — русский.

Запуск: `Start.bat` (Docker) или `Start.bat local` (в памяти, нужен только токен).
Гейт качества: `pwsh -File scripts/ci.ps1`.

## 1. Карта файлов

**Корень.** `README.md` (обзор, EN) · `Claude.md` (этот файл) · `Roadmap.md` (план
ОБТ) · `Narrative.md` (мир и тон текста) · `pyproject.toml` (зависимости, ruff,
mypy, pytest) · `alembic.ini` · `Dockerfile` · `docker-compose.yml` ·
`docker-compose.prod.yml` · `Start.bat` · `stop.bat` · `.env.example` · `uv.lock`.

**`src/mmorpg/`** — код.
- Корень пакета: `main.py` (композиция, polling/webhook), `config.py` (Settings,
  единственный доступ к env), `logging.py`, `monitoring.py` (детектор медленных
  колбэков), `health.py` (heartbeat).
- `domain/` — чистая логика, только stdlib, без async и I/O.
  `entities/`: `character.py`, `stats.py`, `combat.py`, `effects.py`,
  `location.py`, `content.py`. `rules/`: `combat.py` (движок боя), `stats.py`,
  `progression.py`, `economy.py`, `modifiers.py`, `skill_effects.py` (эффект →
  спецификация). `procgen/`: `seeds.py`, `location.py`, `enemies.py`.
  `ports/repositories.py` — протоколы хранилищ.
- `application/` — `dto/creation.py` (черновик персонажа), `services/`.
- `infrastructure/` — `persistence/`: `postgres.py`, `memory.py`, `pool.py`;
  `cache/`: `redis_cache.py`, `memory.py`; `content/loader.py` (TOML → dataclass).
- `presentation/telegram/` — `handlers/` (`creation.py`, `play.py`), `flows/`
  (`creation.py`, `play.py`, `combat.py` — чистые автоматы), `screens/`
  (`base.py`, `format.py`, `paginated.py`, `creation.py`, `play.py`, `combat.py`,
  `shop.py`, `settings.py`), `keyboards/` (`labels.py`, `reply.py`),
  `middlewares/` (`dependencies.py`, `errors.py`, `idempotency.py`),
  `states/screens.py`, `routing.py`, `messaging.py`, `broadcast.py` (канал).

**`content/`** — `world.toml` (15 городов × 5 локаций, уровни 1–300),
`races.toml`, `classes.toml`, `traits.toml`, `skills.toml`, `items.toml`,
`enemies.toml`. Правится без кода, валидируется на старте.

**`docs/`** — `architecture.md`, `accessibility.md` (спецификация, не пожелания),
`procgen.md`, `content-guide.md`, `skills.md`, `deployment.md`,
`release-checklist.md`, `adr/0001..0005` (по решению на файл).

**`tests/`** — `domain/` (7 файлов, включая `test_layering.py`), `content/` (4),
`presentation/` (8, включая `test_accessibility.py` и `test_broadcast.py`),
`application/`, `integration/` (Postgres и Redis, маркер `integration`),
`test_config.py`, `test_health.py`, `test_main.py`, `conftest.py`.

**`scripts/`** — `ci.ps1` / `ci.sh` (гейт), `healthcheck.py`, `broadcast.py`
(пост в канал), `install-hooks.ps1` / `.sh`. **`migrations/`** — `env.py`,
`versions/0001_initial_schema.py`. **`.githooks/pre-commit`** — гейт на коммите.

## 2. Правила разработки

1. **Направление зависимостей**: presentation → application → domain;
   infrastructure реализует порты. Домен синхронный, без I/O, без `datetime.now`,
   без глобального `random` — сид и цикл приходят аргументом. Проверяет
   `tests/domain/test_layering.py`.
2. **Доступность — блокер.** Только reply-клавиатуры, никаких inline и
   `edit_message`. Одно действие — одно новое сообщение. Первая строка отвечает
   «где я / что случилось». Псевдографика запрещена, числа — `X из Y`. Последний
   ряд всегда `Назад · Осмотреться · Главное меню`. Каждое действие дублируется
   текстовой командой. `parse_mode=None`. Полный список — `docs/accessibility.md`.
3. **Никаких таймеров в PvE.** Таймер хода есть только на арене (60 с) с
   автодействием из настроек.
4. **Текст игрока — по `Narrative.md`.** Сверяться до написания, не после.
5. **Логика — не в хендлерах.** Хендлер: разобрать кнопку, вызвать сервис,
   отрисовать экран. Запись в БД оформляется намерением на состоянии флоу.
6. **Контент — в TOML, не в коде.** Новое умение — строка в `skills.toml`; новый
   вид поведения — одна запись в `skill_effects.py`.
7. **Ничего производного не хранится**: тотал статов, лут, ассортимент и карта
   считаются заново из сида и цикла. Ключи Redis всегда с TTL.
8. **Новый экран** добавляется в `tests/presentation/conftest.py::all_screens` —
   иначе он не проверен.
9. **Тесты обязательны**: домен ≥ 90 % покрытия, каждый новый SQL — в
   `tests/integration/`, каждый бродкаст — в `test_broadcast.py`.
10. **Документация и `Roadmap.md` обновляются в том же коммите**, что и код.
    Лимит каждого документа — 5000 символов. Спорное решение — ADR.
11. **Коммиты** — conventional commits на английском, гейт не пропускать
    (`--no-verify` только с явного разрешения).

## 3. Перед коммитом

`pwsh -File scripts/ci.ps1` зелёный · экран добавлен в `all_screens` · флаги в
`Roadmap.md` обновлены · тексты сверены с `Narrative.md` · миграция есть, если
появилась колонка · `docs/release-checklist.md` пройден для игрового изменения.
