# ADR 0040 — Правки смотрителя ложатся на опорные числа игры

Статус: принято (2026-08-29)
Опирается на ADR 0008 (правки смотрителя — поверх `content/`, не вместо),
ADR 0039 (первый заход «Overlay на остальной контент»: `trait`, `craft`,
`recipe`), правило 7 `Claude.md` (в контенте нет числа, которого движок не
считает; кривую диапазона несёт не написанная цифра, а таблица).

## Обстоятельства

`Roadmap.md` держит «Панель смотрителя — до полной админки» отдельной дорожкой.
Первый её заход по overlay (ADR 0039) добавил `trait / craft / recipe`. Осталось
`skill`, `turning` и **`meta`** — тюнинг-константы `content.rules`
(`ProgressionRules`). Это следующий заход, самый узкий из трёх.

`ProgressionRules` собирается из `content/classes.toml [progression]` и правится
только там, с перезапуском. А это ровно те ручки, по которым «Чем правят баланс»
(`Roadmap.md`) велит читать первые сутки открытого теста: `[meta].rank_costs`,
`[meta].branch_gates`, очки за уровень.

Таблица `content_overlay` — по ключу `(kind, entity_id)`, `kind TEXT`, без
`CHECK`. Новая разновидность миграции не требует.

## Решение

**Одна разновидность `OverlayKind.META`, одна сущность, ключ `"rules"`
(`overlay.META_ID`).** Опорные числа в игре одни — заводить вторые нельзя
(`META` не в `CREATABLE`) и убирать нечего (`META` в `NON_REMOVABLE`:
`_removal_problems` отказывает, кнопки «Убрать из игры» на карточке нет). Карточка
рисуется тем же кодом, что у остальных: `listing` отдаёт одну строку, `snapshot`
— текущие числа из `content.rules`.

**Белый список, а не всё подряд.** Правятся только числа, которые двигают баланс:
`base_stat_value`, `free_points_at_creation`, `stat_points_per_level`,
`skill_point_per_level`, `rank_costs`, `branch_gates`. За файлами остаётся то, что
**держит дорогу**: число уровней, счёт слотов, уровни развилок и ступеней ветви,
списки уровней открытия умений — их сдвиг ломает сохранённое состояние и
структуру экранов, а не баланс.

**Новый вид поля `FieldKind.NUMBERS` — «список целых через запятую».** `1, 2, 2,
3, 4` — цена рангов; `0, 6, 14, 24` — очки на ступени ветви.
`OverlayRecord.numbers(key)` разбирает строку, кривой сегмент выпадает, ругается
валидатор. Набирают сообщением (как `TEXT`), значение заменяет поле целиком.

**Проверка (`_meta_problems`).** Скаляр: не меньше нуля, не больше
`META_CEILING` (100 — тюнинг это сдвиг на проценты, а не замена правил). Список:
без отрицательных, не длиннее `META_LIST_LIMIT` (12). `rank_costs` — не короче
`max_rank` (иначе `rank_cost` не покрывает верхние ранги). `branch_gates` —
первое число `0` (первая ступень открыта сразу) и по возрастанию.

**`apply()` — порядок.** Опорные числа ни от чего не зависят, встают первыми,
рядом с чертами и ремёслами. `rules` протянут во все вызовы `_rebuilt`
(параметр `rules: ProgressionRules | None`). Незаполненное поле оставляет то, что
в файлах, — правка меняет ровно названное.

`skill` и `turning` — следующие заходы: у обоих вложенные структуры (грани с
`EdgeEffect`, список вариантов Палаты), под которые нужен ещё один вид поля.

## Последствия

- `domain/entities/overlay.py` — `OverlayKind.META`, `OverlayRecord.numbers`.
- `domain/rules/overlay.py` — `FieldKind.NUMBERS`, `META_ID / META_CEILING /
  META_LIST_LIMIT`, `NON_REMOVABLE`, ветки в `TITLES / FIELDS / shown / listing /
  snapshot / _field_problems / _shape_problems / _removal_problems / apply`,
  `_meta_fields / _meta_problems / _numbers_problems / _apply_meta / _rules_from`,
  `_rebuilt(rules=…)`.
- `presentation/telegram/screens/keeper.py` — `KINDS` +1, `_how_to_fill` и снятие
  кнопки «Убрать» для `NON_REMOVABLE`.
- `presentation/telegram/flows/keeper.py` — `NUMBERS` в наборе `_step_entity`,
  отказ на «Убрать» для `NON_REMOVABLE`.
- Миграции нет. `content/changelog.toml` не трогается — панель игроку не видна.
