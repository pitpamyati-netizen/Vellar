# Claude.md — карта проекта и правила разработки

Vellar — текстовая MMORPG в Telegram для незрячих игроков. Python 3.14, aiogram 3,
PostgreSQL, Redis, гексагональная архитектура. Весь текст игрока — русский.

Запуск: `Start.bat` (Docker) или `Start.bat local` (в памяти, нужен только токен).
Гейт качества: `pwsh -File scripts/ci.ps1`.

## 1. Карта файлов

**Корень.** `README.md` (обзор, EN) · `Claude.md` · `Roadmap.md` (план ОБТ) ·
`Narrative.md` (мир и тон) · `pyproject.toml` (зависимости, ruff, mypy, pytest) ·
`alembic.ini` · `Dockerfile` · `docker-compose*.yml` · `Start.bat` · `stop.bat` ·
`.env.example` · `uv.lock`.

**`src/mmorpg/`** — код.
- Корень пакета: `main.py` (композиция, polling/webhook), `config.py` (Settings,
  единственный доступ к env), `logging.py`, `monitoring.py` (детектор медленных
  колбэков), `health.py` (heartbeat).
- `domain/` — чистая логика, только stdlib, без async и I/O.
  `entities/`: `character`, `stats`, `combat`, `effects`, `location`, `content`.
  `rules/`: `combat` (движок боя), `stats`, `progression`, `economy`,
  `modifiers`, `skill_effects` (эффект → спецификация), `group_commands`
  (грамматика группы), `group_offers` (предложения). `procgen/`: `seeds`,
  `location`, `enemies`. `ports/repositories.py` — протоколы хранилищ.
- `application/` — `dto/creation.py` (черновик), `services/`: `group_trade.py`
  (операции группы), `offers.py` (предложения в кэше).
- `infrastructure/` — `persistence/`: `postgres`, `memory`, `pool`; `cache/`:
  `redis_cache`, `memory`; `content/loader.py` (TOML → dataclass),
  `content/changelog.py` (обновления для канала).
- `presentation/telegram/` — `handlers/` (`creation`, `play`, `group`), `flows/`
  (`creation`, `play`, `combat` — чистые автоматы), `screens/` (`base`, `format`,
  `paginated`, `creation`, `play`, `combat`, `shop`, `settings`, `group`),
  `keyboards/` (`labels`, `reply`), `middlewares/` (`dependencies`, `errors`,
  `idempotency`), `states/screens.py`, `routing.py`, `messaging.py`,
  `broadcast.py` (канал), `throttle.py` (лимит), `cleanup.py` (уборка в группе).

**`content/`** — `world.toml` (15 городов × 5 локаций, 1–300), `races.toml`,
`classes.toml`, `traits.toml`, `skills.toml`, `items.toml`, `enemies.toml`.
Правится без кода, валидируется на старте. Отдельно — `changelog.toml`: что
изменилось, словами игрока; читается только при посте в канал.

**`docs/`** — `architecture.md`, `accessibility.md` (спецификация, не пожелания),
`procgen.md`, `content-guide.md`, `skills.md`, `deployment.md`,
`release-checklist.md`, `adr/0001..0005`.

**`tests/`** — `domain/` (9, слои держит `test_layering.py`), `content/` (5),
`presentation/` (9: доступность, канал, группа), `application/` (2),
`integration/` (маркер `integration`), `test_config.py`, `test_health.py`,
`test_main.py`, `conftest.py`.

**`scripts/`** — `ci.ps1`/`ci.sh` (гейт), `healthcheck.py`, `broadcast.py` (пост
в канал: `--headline` или `--changelog latest`), `install-hooks.ps1`/`.sh`.
**`migrations/`** — `env.py`, `versions/0001_initial_schema.py`.
**`.githooks/pre-commit`** — гейт на коммите.

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
3. **Никаких таймеров в PvE.** Только арена: 60 с и автодействие из настроек.
4. **Текст игрока — по `Narrative.md`.** Сверяться до написания, не после.
5. **Логика — не в хендлерах.** Хендлер: разобрать кнопку, вызвать сервис,
   отрисовать экран. Запись в БД — намерением на состоянии флоу.
6. **Контент — в TOML, не в коде.** Новое умение — строка в `skills.toml`, новое
   поведение — запись в `skill_effects.py`.
7. **Ничего производного не хранится**: тотал статов, лут, ассортимент и карта
   считаются заново из сида и цикла. Ключи Redis всегда с TTL.
8. **Новый экран** добавляется в `tests/presentation/conftest.py::all_screens` —
   иначе он не проверен. **Группа — не экран**: там нет служебного ряда и нет
   «Назад», бот отвечает только на reply и молчит на всё остальное.
9. **Тесты обязательны**: домен ≥ 90 % покрытия, новый SQL — в
   `tests/integration/`, новый бродкаст — в `test_broadcast.py`.
10. **Документация и `Roadmap.md` обновляются в том же коммите**, что и код.
    Лимит документа — 5000 символов. Спорное решение — ADR.
11. **Коммиты** — conventional commits на английском; `--no-verify` только
    с явного разрешения.

## 3. Перед коммитом

`pwsh -File scripts/ci.ps1` зелёный · экран добавлен в `all_screens` · флаги в
`Roadmap.md` обновлены · тексты сверены с `Narrative.md` · миграция есть, если
появилась колонка · `docs/release-checklist.md` пройден для игрового изменения.
