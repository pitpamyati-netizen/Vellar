# ADR 0055 — У округи есть состояние

Статус: принято (2026-09-01)

## Обстоятельства

Карта Vellar перекладывается по выработке (ADR 0035), узлы держат волны
(ADR 0013), в локациях оседают блуждающие ходы (ADR 0037). Всё это уже
посчитано и лежит в `LocationState`. Но **никто, кроме самой карты, это
состояние не читает**: цены в городе, ассортимент лавки, шанс прозвищ у врагов,
цели сводки — всё это одинаково во все стороны и не зависит от того, выбита
округа или свежа. Мир меняет раскладку, но не поведение: игрок, вернувшийся в
пройденную локацию, видит другие тропы — и ровно ту же экономику.

Сводка (ADR 0053, 0054) дала карте подсвеченные точки. Следующий шаг — чтобы у
самих мест был характер, за которым стоит настоящая механика.

## Решение

**`domain/rules/mood.py::mood_of(state: LocationState) -> LocationMood`** —
чистая функция от уже посчитанного состояния. Четыре состояния, от свежего к
вычищенному:

- `UNTOUCHED` — нетронута: снятых волн меньше `WORKED_AT` (`REGROWTH_WAVES // 2`);
- `WORKED` — хожена: снято больше порога, но поколение ещё то же;
- `DEPLETED` — выработана: `location_epoch(state) >= 1`, округа переложилась;
- `RESTLESS` — встревожена: в локации осел блуждающий ход (`state.roamer`).
  Перебивает всё: это самый громкий след.

`worked_units` = `sum(node.wave + node.taken)` — прошедшие волны плюс взятое из
текущих.

**Слово читает игрок** на экране локации (`screens/play.location_screen`, новый
параметр `mood`, строка `MOOD_LINE`; «встревожена» отдельной строкой не пишется —
блуждающий ход и так описан). Дальше состояние правит механику — три захода,
каждый свой коммит:

1. **Цели сводки** (сделано). `digest(moods=...)` — необязательный аргумент
   `слот → LocationMood`. `_pick_spot` выбирает локацию под `HUNT`/`CULL`/
   `SEARCH` через `random.choices` с весом по состоянию (`_MOOD_WEIGHT`, пологий:
   2/3/4/5). `random.choices` тратит из сида ровно один вызов независимо от
   весов, поэтому `HAUL` и `DELVE` от состояния не зависят вовсе, а расхождение
   между экраном и зачётом возможно только у локационных дел и только если округа
   сменила настроение между тем и другим — тогда дело не засчитается, платы из
   ниоткуда нет. Живое состояние строит `digest_claim.city_moods`; читают его
   одинаково `_digest_view` (→ `DigestView.moods` → рендер) и `_pay_digest` /
   `_pay_digest_search`.
2. **Шанс прозвищ врагов** (сделано). `dungeon.affix_odds(rank, mood)` прибавляет
   к базовому шансу `_MOOD_AFFIX_BUMP` (0 / 0,10 / 0,20 / 0,25) — но только эпику
   и хозяину логова. Обычная стая прозвищ не получает никогда (ADR 0042), и
   выработка этого не меняет. `handlers/combat._spawn` передаёт
   `mood_of(location_state)`.
3. **Цены и ассортимент ближайшего города** (сделано). `mood.city_strain`
   усредняет `_STRAIN` (0 / 0,15 / 0,5 / 0,4) по локациям города → 0…1.
   `economy.roll_assortment(strain=)` сужает прилавок (`STRAIN_STOCK_LOSS`, но не
   ниже `STOCK_MIN`), `buy_price(strain=)` поднимает цену (`STRAIN_PRICE_MARKUP`,
   до +50%). `handlers/play._goods` считает `strain` только на экране лавки. Лавка
   и без того перебрасывается каждый переворот и проверяется в том же запросе,
   поэтому расхождения нет.

**Ничего не хранится.** `mood_of` — функция от `LocationState`, а он и так живёт
в кэше со сроком (ADR 0037), как волны узлов. В БД ничего, миграции нет.

## Последствия

- `domain/rules/mood.py` — новый модуль: `LocationMood`, `mood_of`,
  `worked_units`, `WORKED_AT`.
- `presentation/telegram/screens/play.py` — `location_screen(mood=...)`,
  `MOOD_LINE`; `flows/play.py` — `mood_rules.mood_of(here_now)` в рендере
  `LOCATION`.
- `domain/rules/digest.py` — `digest(moods=...)`, `_pick_spot`, `_MOOD_WEIGHT`.
- `presentation/telegram/digest_claim.py` — `city_moods`; `screens/city.py` —
  `DigestView.moods`; `handlers/{play,combat}.py` — строят `city_moods` и
  прокидывают в `digest()` рядом с зачётом дела.
- `domain/rules/dungeon.py` — `affix_odds(rank, mood)`, `_MOOD_AFFIX_BUMP`;
  `handlers/combat._spawn` передаёт `mood_of(location_state)`.
- `domain/rules/mood.py` — `city_strain`, `_STRAIN`; `domain/rules/economy.py` —
  `roll_assortment(strain=)`, `buy_price(strain=)`, `STRAIN_*`;
  `handlers/play._goods` считает `strain` из `city_moods` на экране лавки.
- Тесты — `tests/domain/test_mood.py`, `tests/domain/test_digest.py` (смещение и
  неизменность `HAUL`/`DELVE`), `tests/domain/test_dungeon.py` (прибавка к шансу
  прозвища), `tests/presentation/test_combat_shop_flow.py` (цена и ширина
  прилавка), экран с непустым `mood` в `conftest.all_screens`.
- Docs — этот ADR, `Claude.md`, `Roadmap.md`, `content/changelog.toml` (на
  каждом заходе, где игрок что-то заметит).

## Что рассматривали ещё

- **Считать активность игроков отдельным счётчиком в кэше.** Отвергнуто:
  `LocationState` уже несёт всё нужное (волны, поколение, роамер), а лишний
  ключ — это лишний срок и лишняя рассинхронизация.
- **Больше состояний (пять-шесть градаций).** Отвергнуто: игрок должен различать
  их на слух одной фразой, а механике хватает «свежо / хожено / выбито / тут
  что-то не так».
- **`mood` как поле `LocationState`.** Отвергнуто: это производное, а производное
  не хранят (`Claude.md`, правило 8). Считается на месте отрисовки и на месте
  сборки сводки.
