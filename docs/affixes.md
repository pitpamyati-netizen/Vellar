# Прозвища-модификаторы врагов

Прозвище — приставка к имени породы и настоящая механика в бою: та же тварь, но
опаснее. См. ADR 0042; где встречаются — `docs/dungeons.md`.

## Форма (`content/enemies.toml`, `[[affix]]`)

```toml
[[affix]]
id = "venombite"           # замороженный ключ, лежит на Enemy.affixes
adjective = "Гнилозубый"    # приклеивается перед именем породы
weight = 4                  # вес на взвешенном броске
health = 1.05               # множители: запекаются в числа при сборке, как stakes
damage = 1.05              # (по умолчанию 1.0; в рамках 0.5..2.5)
armor = 1.0
initiative = 1.0
gold = 1.35
on_hit_status = "poison"    # состояние, которое движок вешает на цель по попаданию
on_hit_turns = 3
on_hit_chance = 55.0
# on_hit_magnitude = 20.0   # величина состояния; для яда (DOT) 0 = «от силы удара»
# pack_bonus = 2            # «выводковый»: лишние тела в стае
# recloak = 2               # «неуловимый»: заходит незаметным, прячется снова
# [affix.modifiers]         # прибавки на весь бой; ключи только из EFFECTIVE_KEYS
# reflect_percent = 25.0
```

## Как работает механика

Делится надвое (`domain/rules/modifiers.EFFECTIVE_KEYS` — тот же список, что для
умений: прибавка, которой движок не читает, — не прибавка, ADR 0018):

- **`modifiers`** → `EnemyAffix.effect()` — `ActiveEffect(permanent=True)`,
  навешивается на конкретного бойца-породу в `application/services/battle.begin`
  (не через `opening_effects` — те по стороне). Что уже работает у породы:
  `reflect_percent`, `lifesteal_percent`, `armor_percent`, `initiative_percent`,
  `damage_taken_percent`, `accuracy_percent`, `dodge_percent`
  (`_dodge_of` дополнен ради «верткого»).
- **`on_hit_status`** → `combat._affix_on_hit`, вызывается в `_strike` после
  состоявшегося удара породы и только на прямой удар, не на ответный
  (`answering`): размен статусов не должен множиться. Величина: для DOT
  (`poison`/`bleeding`/`burning`) — от силы удара, если `on_hit_magnitude` не
  задан; для прочих (`slow`, `weakness`) — из `on_hit_magnitude`.
- **`pack_bonus`** → `generate_group` прибавляет тел к стае (потолок 5).
- **`recloak`** → `combat._recloaked` (ADR 0043). Стая заходит в бой с
  `StatusKind.UNSEEN` (`battle.begin`), и через `recloak` её ходов после того,
  как её выдали, уходит из виду снова — откатом `affix:recloak`. Тот ход, где её
  выдал собственный удар, окна не отнимает (`was_unseen`). Контра — удар по
  всем, дот и световая граната (`flash_grenade`).

## Кто бросает и где

Бросок один на всю стаю (`generate_group._roll_affixes`) — вся стая с одной
приставкой и одними эффектами.

| контекст | шанс / число |
| --- | --- |
| разведка | 0 |
| тёмный ход (`delve`) | 35 % / 1 (`DifficultySpec.affix_chance/affix_count`) |
| гиблый спуск (`grim`) | 70 % / 2 |
| эпик-узел локации | 50 % / 1 (`dungeon.NODE_AFFIX_ODDS`) |
| босс-узел локации | 90 % / 1 |
| обычная стая на дороге | никогда (`affix_chance = 0`) |

Выбитая и встревоженная округа (`domain/rules/mood.LocationMood`, ADR 0055)
прибавляет к шансу эпик- и босс-узла `_MOOD_AFFIX_BUMP` (0 / 0,10 / 0,20 / 0,25
по состоянию, потолок 1,0). Обычной стаи это не касается — у неё прозвищ нет.

## Экран

Имя врага несёт приставку. Плюс строка на каждое уникальное прозвище живых
врагов: `screens/combat.affix_lines` → `screens/dungeon.affix_line`
(`AFFIX_HINTS` — фиксированный перечень, как `RANK_NAMES`, не правимый контент).

## Правка

Прозвища правит только `content/enemies.toml` — в панель смотрителя они пока не
вынесены (как `skill` и `turning`, ADR 0040: вложенный список — следующий
заход). Флаг `dungeon` у породы правится из панели и переживает round-trip.
