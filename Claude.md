# Claude.md — карта проекта и правила разработки

Vellar — текстовая MMORPG в Telegram для незрячих игроков. Python 3.14, aiogram 3,
PostgreSQL, Redis, гексагональная архитектура. Весь текст игрока — русский.

Запуск: `Start.bat` (Docker) или `Start.bat local` (в памяти, нужен только токен).
Обновить работающую игру, не трогая базу и Redis: `Update.bat`.
Гейт качества: `pwsh -File scripts/ci.ps1`.

## 1. Карта файлов

**Корень.** `README.md` (обзор, EN) · `Claude.md` · `Roadmap.md` (план ОБТ) ·
`Narrative.md` (мир и тон) · `pyproject.toml` (зависимости, ruff, mypy, pytest) ·
`alembic.ini` · `Dockerfile` · `docker-compose*.yml` · `Start.bat` (сборка из
рабочего дерева, штамп версии) · `Update.bat` (обновление работающей игры,
`rollback`) · `stop.bat` (сохранить всё, затем остановить) ·
`.env.example` · `uv.lock`.

**`src/mmorpg/`** — код.
- Корень пакета: `main.py` (композиция, polling/webhook), `config.py` (Settings,
  единственный доступ к env), `logging.py`, `monitoring.py` (детектор медленных
  колбэков), `health.py` (heartbeat).
- `domain/` — чистая логика, только stdlib, без async и I/O.
  `entities/`: `character`, `stats`, `combat`, `effects`, `location`, `content`,
  `quest` (подряд и журнал подрядов), `craft` (ремёсла, рецепты, качество).
  `rules/`: `combat` (движок боя), `tempo` (намерение, след, брешь), `stats`,
  `progression`, `economy`, `modifiers`, `skill_effects` (эффект →
  спецификация), `skills` (изучение, ранги, грани, слоты), `quests` (счёт и
  плата по подрядам), `crafts` (ранг от работы, сбор по личному откату,
  изготовление и качество), `adventure` (последствия боя и узла, ночлег, зелья
  вне боя), `tutorial` (шесть заданий обучения, маска на персонаже), `keeper`
  (служебные выдачи смотрителя), `group_commands` (грамматика группы),
  `group_offers` (предложения).
  `procgen/`: `seeds`, `location`, `enemies`. `ports/repositories.py` —
  протоколы хранилищ.
- `application/` — `dto/creation.py` (черновик), `services/`: `group_trade.py`
  (операции группы), `offers.py` (предложения в кэше), `keeper.py` (сверка флага
  смотрителя с `ADMIN_IDS`).
- `infrastructure/` — `persistence/`: `postgres`, `memory`, `pool`; `cache/`:
  `redis_cache`, `memory`; `content/loader.py` (TOML → dataclass),
  `content/changelog.py` (обновления для канала).
- `presentation/telegram/` — `handlers/` (`creation`, `play`, `combat`, `group`;
  бой включается перед `play`, иначе его перехватит фильтр по группе состояний),
  `flows/` (`creation`, `play`, `combat` — чистые автоматы), `screens/` (`base`,
  `format`, `paginated`, `creation`, `play`, `combat`, `shop`, `skills`,
  `quests`, `crafts`, `city`, `settings`, `tutorial`, `keeper`, `group`),
  `keyboards/` (`labels`, `reply`), `middlewares/` (`dependencies`, `errors`,
  `idempotency`), `states/screens.py`, `routing.py`, `messaging.py`,
  `broadcast.py` (канал), `throttle.py` (лимит), `cleanup.py` (уборка в группе).

**`content/`** — `world.toml` (15 городов × 5 локаций, 1–300), `races.toml`
(16 рас), `classes.toml` (8 классов), `traits.toml`, `skills.toml`, `items.toml`,
`enemies.toml`, `quests.toml` (подряды акта I), `crafts.toml` (ремёсла и
рецепты).
Правится без кода, валидируется на старте. Отдельно — `changelog.toml`: что
изменилось, словами игрока; читается только при посте в канал.

**`docs/`** — `architecture.md`, `accessibility.md` (спецификация, не пожелания),
`procgen.md`, `content-guide.md`, `skills.md`, `crafts.md`, `keeper.md`,
`deployment.md`, `release-checklist.md`, `adr/0001..0007`
(`0003` — поколения локаций вместо шестичасовой стражи).

**`tests/`** — `domain/` (слои держит `test_layering.py`, длину боя —
`test_combat_balance.py`), `content/`, `presentation/` (доступность, канал,
группа, сквозной проход по циклу в `test_adventure_flow.py`), `application/`,
`integration/` (маркер `integration`), `test_config.py`, `test_health.py`,
`test_main.py`, `conftest.py`.

**`scripts/`** — `ci.ps1`/`ci.sh` (гейт), `healthcheck.py`, `broadcast.py` (пост
в канал: `--headline` или `--changelog latest`), `install-hooks.ps1`/`.sh`,
`vellar-tools.bat` (общие подпрограммы трёх .bat: штамп версии, дамп, SAVE).
**`migrations/`** — `env.py`, `versions/0001_initial_schema`, `0002_trades`,
`0003_privacy`, `0004_wounds_bank_quests`, `0005_crafts`, `0006_admin`,
`0007_tutorial`.
**`backups/`** — дампы от `stop.bat` и `Update.bat`; не в репозитории.
**`.githooks/pre-commit`** — гейт на коммите.

## 2. Правила разработки

1. **Направление зависимостей**: presentation → application → domain;
   infrastructure реализует порты. Домен синхронный, без I/O, без `datetime.now`,
   без глобального `random` — сид, поколение локации и время приходят
   аргументом. Проверяет
   `tests/domain/test_layering.py`.
2. **Доступность — блокер.** Только reply-клавиатуры, никаких inline и
   `edit_message`. Одно действие — одно новое сообщение. Первая строка отвечает
   «где я / что случилось». Псевдографика запрещена, числа — `X из Y`. Последний
   ряд всегда `Назад · Главное меню`. Каждое действие дублируется
   текстовой командой. `parse_mode=None`. Полный список — `docs/accessibility.md`.
3. **Никаких таймеров в PvE.** Только арена: 60 с и автодействие из настроек.
   Длина боя — три хода на обычном противнике, вдвое на эпическом, вчетверо на
   боссе; держит `tests/domain/test_combat_balance.py`.
4. **Текст игрока — по `Narrative.md`.** Сверяться до написания, не после.
5. **Логика — не в хендлерах.** Хендлер: разобрать кнопку, вызвать сервис,
   отрисовать экран. Запись в БД — намерением на состоянии флоу.
6. **Раса, класс и ремесло — три разные оси.** Раса — кто персонаж такой,
   класс — как он дерётся, ремесло — что он делает руками (`crafts.toml`).
   Классом ремесло не называют и наоборот; держит `tests/content/test_naming.py`.
   Игрок — приключенец: он нигде не рождается и никуда не приписан.
7. **Контент — в TOML, не в коде.** Новое умение — строка в `skills.toml`, новое
   поведение — запись в `skill_effects.py`. Сила умения — всегда процент: урон от
   стандартного удара, лечение и щит от максимума здоровья, усиление от самого
   модификатора. Абсолютных чисел в контенте нет (ADR 0007).
8. **Ничего производного не хранится**: тотал статов, лут, ассортимент и карта
   считаются заново из сида. Карта локации живёт до зачистки и общая для всех,
   кто в ней (ADR 0003); стражи в мире нет. Ключи Redis всегда с TTL. **Сохранённому
   состоянию не верят**: оно переживает и код, и контент, поэтому экран, чей
   город, умение, подряд или вылазка больше не существуют, не падает, а
   возвращает игрока на живой экран (`flows/play.py`, `known_city`,
   `location_known`).
9. **Новый экран** добавляется в `tests/presentation/conftest.py::all_screens` —
   иначе он не проверен. **Группа — не экран**: там нет служебного ряда и нет
   «Назад», бот отвечает только на reply и молчит на всё остальное.
10. **Тесты обязательны**: домен ≥ 90 % покрытия, новый SQL — в
   `tests/integration/`, новый бродкаст — в `test_broadcast.py`.
11. **Документация и `Roadmap.md` обновляются в том же коммите**, что и код.
    Лимит документа — 5000 символов. Спорное решение — ADR.
12. **Коммиты** — conventional commits на английском; `--no-verify` только
    с явного разрешения.

## 3. Перед коммитом

`pwsh -File scripts/ci.ps1` зелёный · экран добавлен в `all_screens` · флаги в
`Roadmap.md` обновлены · тексты сверены с `Narrative.md` · миграция есть, если
появилась колонка · `docs/release-checklist.md` пройден для игрового изменения.
