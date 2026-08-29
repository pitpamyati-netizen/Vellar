# ADR 0039 — Правки смотрителя ложатся на черты, ремёсла и рецепты

Статус: принято (2026-08-29)
Опирается на ADR 0008 (правки смотрителя — поверх `content/`, не вместо),
ADR 0007 и правило 7 `Claude.md` (в контенте нет прибавки, которой движок не
считает: проверять — по `modifiers.EFFECTIVE_KEYS`, а не по широкому словарю
`traits.toml`).

## Обстоятельства

`OverlayKind` (`domain/entities/overlay.py`) знал только `npc`, `quest`,
`location`, `enemy`, `city`. Прибавку черты, название ремесла и состав рецепта
правили только в `content/*.toml` с перезапуском. `Roadmap.md` держит это
отдельной дорожкой («Панель смотрителя — до полной админки»), и первый её заход —
`trait` + `craft` + `recipe`. `skill`, `turning` и тюнинг-константы (`meta`) —
следующие заходы.

Таблица `content_overlay` — JSONB по ключу `(kind, entity_id)`, поэтому новые
разновидности миграции не требуют. Всё новое — в домене и презентации.

Побочно: `overlay._rebuilt` собирал `GameContent` **без**
`gear_tiers / gear_archetypes / special_properties / turnings / open_turning_id`.
Любая правка смотрителя роняла снаряжение глубокого спуска и голосования Палаты
из «current»-мира до перезапуска.

## Решение

**Три разновидности, все заводятся с нуля.** `OverlayKind.TRAIT / CRAFT / RECIPE`
в `CREATABLE`. Как у жителей и заданий: пустая запись пишется, но не применяется,
пока в ней есть отказ (`overlay.problems`).

**Новый вид поля `FieldKind.PAIRS` — «ключ=число».** Одна форма на два случая:
прибавки черты (`modifiers`) и состав рецепта (`inputs`). Ключ выбирается из
известного списка, значение набирают. У `FieldSpec` — `pair_value`
(`NUMBER` для счёта в рецепте, `RATE` для доли прибавки, знак допустим).
`OverlayRecord.pairs(key)` разбирает `"stat_STR=2, armor_percent=-5"`; кривой
сегмент выпадает, ругается валидатор — как везде с тем, что пришло строкой.

**Ключи прибавок черты проверяются по `EFFECTIVE_KEYS`** (`Source.MODIFIER`), а
не по словарю `traits.toml [meta].modifier_keys`: словарь нарочно шире, в нём
есть ключи под механику, которой пока нет (ADR 0018). Смотритель через панель
такую «прибавку-обещание» повесить не может.

**Что правится, а что нет.**
- Черта: название, раздел, текст, прибавки. Теги (`Trait.tags`) правила не
  читают — правка их сохраняет, заведённая черта получает `tags=()`.
- Ремесло: название, вид (сбор/работа), характеристика, описание. Находки сбора
  (`Craft.yields`) — вложенные списки биомов — остаются в `crafts.toml`; правка
  существующего сбора их сохраняет, заведённое ремесло начинается без них.
- Рецепт: ремесло (только «работа» — `Source.CRAFT` отдаёт `CraftKind.MAKING`),
  ранг (1..`craft_rules.max_rank`), состав, что выходит, сколько, опыт.

**`apply()` — порядок.** Черты и ремёсла ни от чего не зависят, встают первыми;
рецепты зависят и от ремёсел, и от вещей — идут после. Новые параметры
протянуты во все вызовы `_rebuilt`.

**`_rebuilt` теперь несёт весь `GameContent`** — добавлены недостающие
справочники и `turnings`. Регрессия закрыта тестом в `tests/domain/test_overlay.py`.

## Последствия

- `domain/entities/overlay.py` — `OverlayKind.{TRAIT,CRAFT,RECIPE}`,
  `OverlayRecord.pairs`.
- `domain/rules/overlay.py` — `FieldKind.PAIRS`, `FieldSpec.pair_value`,
  `Source.{MODIFIER,CRAFT,TRAIT_CATEGORY}`, ветки в `TITLES / CREATABLE / FIELDS /
  options / option_name / shown / listing / snapshot / problems / apply`,
  `_apply_traits / _apply_crafts / _apply_recipes`, починка `_rebuilt`.
- `domain/entities/content.py` — `GameContent.has_trait`.
- `presentation/telegram/screens/keeper.py` — `KINDS` +3, `_pairs_screen`,
  `pair_from_button`, `_how_to_fill` для `PAIRS`.
- `presentation/telegram/flows/keeper.py` — `PAIRS` в `_step_entity` (набор),
  `_step_field` (снятие пары нажатием), `_value` → `_paired_value` (пара
  ложится, экран поля не разматывается), дефолты `_blank`.
- Миграции нет. `content/changelog.toml` не трогается — панель смотрителя игроку
  не видна.
