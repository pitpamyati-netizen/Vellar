# Проверка и переработка игровых текстов, 15 октября 2026 года

## Граница этой проверки

Это протокол литературной части общей переработки развития, сложности и добычи.
Он относится к текстам городских заданий, мира, рас, домов, ремёсел, общих дел,
долгих целей, экспедиции и к сообщениям интерфейса. Изменения классов, умений,
черт, талантов, специализаций, предметов и боевого баланса описываются отдельно.

Основание: [Narrative.md](../Narrative.md),
[правила доступности](accessibility.md), навыки `russian-literary-writing`,
`text-mmorpg-quests` и `text-game-accessibility`. Цель — понятная работа с ценой,
местом и результатом, естественная русская речь, сохранение канона Веллара.
Знакомые и однозначные названия кнопок оставлены прежними.

## Каталог содержимого

| Файл | Просмотренный объём | Изменено текстовых полей |
| --- | --- | ---: |
| [quests.toml](../content/quests.toml) | Все 57 городских заданий: названия, наниматели, вступления и условия | 117 |
| [world.toml](../content/world.toml) | 15 городов, 75 локаций и 75 подземелий; имена и все существующие описания | 90 |
| [races.toml](../content/races.toml) | Все 16 рас: короткие описания, происхождение, названия и тексты пассивных способностей | 42 |
| [houses.toml](../content/houses.toml) | Все 7 домов и их техники | 7 |
| [expeditions.toml](../content/expeditions.toml) | Все 4 встречи Затопленного штрека | 4 |
| [city_events.toml](../content/city_events.toml) | Два этапа Переправы у Дубно и четыре возможных итога | 6 |
| [long_goals.toml](../content/long_goals.toml) | Путевой двор, два общих решения, три записи хроники | 5 |
| [crafts.toml](../content/crafts.toml) | Названия 11 ремёсел, 15 семейств материалов, рангов и качества; все описания ремёсел | 11 |
| [guilds.toml](../content/guilds.toml) | Названия всех 7 ступеней гильдии | 0 |
| [market.toml](../content/market.toml) | Проверен весь файл: пользовательской прозы в нём нет | 0 |

Всего изменено 282 текстовых поля. Данные до и после редакции сравнены после
разбора TOML: состав таблиц, порядок записей, устойчивые идентификаторы, ссылки,
уровни, числа целей, награды, характеристики и модификаторы этой частью не изменены.
Комментарии о растущей реликтовой награде приведены в соответствие с общей
переработкой добычи; сама ссылка на награду сохранена.

### Что исправлено

- У каждого из 57 нанимателей переписаны вступление и условия. Убраны повторяющиеся
  зачины, неясные местоимения, обязательная острота в конце и обещания действий,
  которых задание не считает.
- Условия называют фактическое число действий и цену. Поисковая цель говорит об
  обследовании мест; победа над сильным противником не выдаётся за отдельное
  пленение, допрос или получение уникальной грамоты.
- У ремесленных заданий прямо сказано, что готовые изделия остаются у мастера.
  Для каждого такого задания названы город работы и требуемое изделие.
- При отправке на спорную землю наниматель заранее предупреждает о нападении
  других путников.
- Первое задание называется «Сверка лугового обхода», поскольку выполняется на
  Лугах у Заставы. Прежний идентификатор `farhold_tallies` сохранён. Уточнены также
  заголовки «Опасность в Оврагах» и «Долговая опись».
- Городские и подземельные описания опираются на место и наблюдаемые предметы.
  Убраны неподдерживаемые обещания успеть между приливами и неверное объяснение
  работы помпы. Описания рас больше не обещают иммунитет к страху или особую
  обязательную судьбу путника.
- У техник домов уточнены первый круг боя, порог четверти здоровья цели и
  прибавки к вероятностям. Тексты пассивных способностей различают ремесленное
  качество, возврат материалов при изготовлении и сырьё, получаемое при разборе.
  У орочьей «Отчаянности» указан действующий порог 35 процентов здоровья вместо
  неточного «ниже трети».

## Достижимость городских заданий

Проверены существование городов, мест, наград и рецептов; уровни предложений;
предшественники и отсутствие циклов. Для каждой боевой цели дополнительно
проверен действующий набор противников в биоме нужной локации: подходящая цель
есть во всех случаях. Существующие проверки генерации подтверждают наличие
нужных поисковых узлов в разных поколениях местности.

Изучены правила `quests.is_open`, `take`, `record_kills`, `record_search`,
`record_craft`, `record_descent` и `hand_in`. Счёт начинается после принятия
работы; отказ не закрывает её навсегда; повторная сдача завершённого задания
не создаёт новую награду. Литературная часть не меняет эти правила и не
переписывает журналы персонажей.

В [test_quest_narrative.py](../tests/content/test_quest_narrative.py) добавлены
четыре общие проверки: совпадение названной цены и числа цели с данными,
предупреждение о спорной земле, сохранение изготовленных вещей у игрока,
наличие требуемых противников именно в нужном месте.

## Сообщения интерфейса

Для исходников построен указатель всех строковых констант с кириллицей:
2 987 записей в 48 файлах из 68 просмотренных файлов. Просмотрены каталоги
`presentation/telegram/flows`, `handlers`, `screens` и `application`.
Пустые служебные модули включены в этот объём. Три экрана — `skills.py`,
`combat.py` и `items.py` — проверяются в частях развития, боя и добычи;
раздел фактического прироста характеристик в `screens/play.py` относится к
переработке развития.

Изменённые сообщения:

| Файл | Практический результат |
| --- | --- |
| [flows/play.py](../src/mmorpg/presentation/telegram/flows/play.py) | Покупка, продажа, надевание, снятие, употребление, разбор и перековка грамматически подходят любому названию вещи; понятнее сообщения о подряде, сборе и настройках |
| [screens/party.py](../src/mmorpg/presentation/telegram/screens/party.py) | Убрано двусмысленное «мест в отряде нет»; объяснены самостоятельные действия героев и полный спуск |
| [screens/city.py](../src/mmorpg/presentation/telegram/screens/city.py) | Прямо объяснены бесплатный и платный отдых, возврат очков у наставника, банк, починка, разбор и перековка |
| [screens/creation.py](../src/mmorpg/presentation/telegram/screens/creation.py) | Число активных слотов читается из правил; объяснение боевого запаса не смешано с пассивными умениями |
| [screens/play.py](../src/mmorpg/presentation/telegram/screens/play.py) | Указано постепенное восстановление здоровья вне боя; убраны обещания фиксированного числа ходов у сильных противников |
| [screens/quests.py](../src/mmorpg/presentation/telegram/screens/quests.py) | Различаются места сдачи городских заданий и испытаний; пустая доска не обещает отсутствующего следующего задания |
| [screens/tutorial.py](../src/mmorpg/presentation/telegram/screens/tutorial.py) | Обучение учитывает восстановление здоровья вне боя |
| [screens/dungeon.py](../src/mmorpg/presentation/telegram/screens/dungeon.py) | Убраны неверный множитель сложности и обещание, что замедление сокращает число ходов; случайное наложение эффекта не выдано за гарантированное |
| [services/city_event.py](../src/mmorpg/application/services/city_event.py) | Сообщение о награде объясняет результат без внутреннего утверждения о защите от повторной записи |
| [services/long_goal.py](../src/mmorpg/application/services/long_goal.py) | Ремесленная подсказка направляет к действующему разделу «Ремёсла» |
| [rules/dungeon.py](../src/mmorpg/domain/rules/dungeon.py) | Все 8 условий спуска называют действительное действие; лечение снижено на 35 процентов, а не «вполовину» |
| [rules/digest.py](../src/mmorpg/domain/rules/digest.py) | Сводка объясняет, какой бой и какая поездка зачтутся; для спуска требуется полное прохождение |

Остальные просмотренные файлы интерфейса и приложения оставлены без литературных
изменений: ясные команды, подтверждения опасных действий, цены и короткие подписи
не нуждаются в замене ради общего количества правок. Действующие маршруты и
текстовые команды сохранены. Ожидания тестов обновлены для нового заголовка
первого задания и нейтральных сообщений о вещах; тест хроники теперь проверяет
вывод действующего текста каталога целиком.

## Проверки и ограничения

Успешно выполнены:

- Проверки каталога `tests/content` и генерации местности `tests/domain/test_procgen.py`.
- Отдельный прогон 457 проверок: городские вещи, панель смотрителя, кузница,
  настройки, сводка, правила сводки и подземелий, новые проверки заданий,
  городское событие, долгие цели, создание героя, доступность и специализации.
- `ruff check` и `ruff format --check` для изменённых этой частью исходников и
  нового файла проверок заданий.
- Разбор TOML и структурное сравнение всех десяти принадлежащих этой части
  каталогов с исходными данными: изменений числовых правил и ключей нет.

После повышения сложности и восстановления повадок противников сценарий
Затопленного штрека проверен через настоящие обработчики: воин 15 уровня,
обычные вещи уровня 12, выбор лекаря и заклинателя через `/цель`, два оплаченных
атакующих умения с передачей выбранной цели, расовое лечение `/раса`.
Все четыре встречи завершены, личный зачёт задания записан; все три исходных
малых зелья остались в сумке. Это один воспроизводимый маршрут данного героя,
а не подтверждение баланса всех классов и начальных значений генератора.
Боевые формулы ради этого прохождения не изменялись.

Дополнительно повторены 12 групповых прохождений модели
`scripts/m06_descent_report.py`: два воина; разбойник, маг и друид; воин,
следопыт, маг, клирик и паладин — по четыре начальных значения генератора
для каждого состава. Все герои имеют 15 уровень и обычные вещи не выше
своего уровня. Модель выбирает изученные умения по состоянию боя, направляет
удары сначала на лекаря и заклинателя и использует доступное расовое лечение.
Все 12 отрядов завершили четыре встречи; каждый участник совершил действие
в каждой встрече и получил полный зачёт. Во всех случаях потрачено ноль
зелий при прежнем пределе три на героя за весь спуск.

Во время этого прогона выявлен отдельный сбой завершения боя: последний
противник мог погибнуть от периодического урона в пропущенный из-за контроля
ход. После исправления общего боевого правила все 12 проходов повторены
без обхода ошибки в модели. Набор `tests/domain/test_participation_m06.py`
прошёл целиком, в том числе проверки предела зелий и действий каждого участника.
Это воспроизводимая проверка трёх составов на четырёх начальных значениях,
а не обещание победы любого отряда при любом выборе действий.

Это проверка кода, содержимого и имитированных действий. Живая игра в Telegram,
прослушивание NVDA/JAWS и принятие текстов незрячими игроками здесь не выполнены.
Интеграционные базы и действующие службы этой частью не затрагивались.

## Актуализация справочников о снаряжении

После общей переработки исправлены действующие
[правила содержимого](content-guide.md), [процедурная сборка](procgen.md),
[прочность](repair.md) и подсказка `class_share` в [карте проекта](project-map.md).
Они описывают постоянную ступень всех вещей, отдельную сохранность максимальной
прочности прежних реликтов, новые оттиски `~v2-…`, бюджеты префиксов и суффиксов
по редкости и вероятностный подбор по классу. Пример первого задания совпадает
с нынешним каталогом. Исправлены устаревшие комментарии `repair.worn_of` и
`loader.RELIC_CHAIN_LENGTH`; механика этими изменениями не затронута.

Из справочника процедурной сборки также убраны прежние числовые формулы врагов,
обещание одинакового трёхходового боя на любом уровне и утверждение, что поход
ничего не сохраняет. Формулы указывают действующие именованные коэффициенты,
получаемые из исходников; длительность боя зависит от подготовки и действий.

В [правилах разработки](development-rules.md) вместо пяти независимых выборов
указаны три ветки по 15 талантов, отдельные очки и подтверждённый переход старого
персонажа со ссылкой на [справочник талантов](talents.md). Датированные протоколы
`verification-skills-2026-10-11.md` и `skills-rework-sources-2026-10-11.md`
сохраняют сведения о проверявшейся тогда версии; текущие правила ими не определяются.

Поиск по всем исходникам, каталогу и Markdown-справочникам не нашёл оставшегося
положительного обещания роста реликта в действующих объяснениях. Прежние обещания
сохранены в исходных решениях ADR 0015, ADR 0052 и ADR 0057, а также в старых
записях `content/changelog.toml`. Это история изменения игры; её задним числом
не переписывают. Новые правила и совместимость явно описаны в
[правилах добычи](loot.md), которые уточняют прежние решения.

Проверены местные ссылки во всех шести затронутых справочниках; все файлы существуют.
Пример задания разобран как TOML и сопоставлен с каталогом по имени, условиям,
месту, цели и наградам. Реальная сборка подтвердила имя примера `~v2-…` и
три числа прочности: 52 для обычной вещи уровня 12, 125 для нового реликта той же
ступени и 456 для прежнего реликта. После этой актуализации успешно выполнены
58 проверок заданий, их текстовых условий, ремонта и новой добычи; комментарии
прошли `ruff check` и `ruff format --check`.

## Перечень просмотренных исходников

Число констант относится к исходному указателю до редакции; комментарии и строки документации исключены. Запись «без правки» относится только к этой литературной части.

| Файл | Констант с кириллицей | Результат |
| --- | ---: | --- |
| [src/mmorpg/application/__init__.py](../src/mmorpg/application/__init__.py) | 0 | Просмотрено, без правки |
| [src/mmorpg/application/battle_snapshots.py](../src/mmorpg/application/battle_snapshots.py) | 0 | Просмотрено, без правки |
| [src/mmorpg/application/delivery.py](../src/mmorpg/application/delivery.py) | 0 | Просмотрено, без правки |
| [src/mmorpg/application/dto/__init__.py](../src/mmorpg/application/dto/__init__.py) | 0 | Просмотрено, без правки |
| [src/mmorpg/application/dto/creation.py](../src/mmorpg/application/dto/creation.py) | 0 | Просмотрено, без правки |
| [src/mmorpg/application/journey.py](../src/mmorpg/application/journey.py) | 0 | Просмотрено, без правки |
| [src/mmorpg/application/operations.py](../src/mmorpg/application/operations.py) | 1 | Просмотрено, без правки |
| [src/mmorpg/application/services/__init__.py](../src/mmorpg/application/services/__init__.py) | 0 | Просмотрено, без правки |
| [src/mmorpg/application/services/battle.py](../src/mmorpg/application/services/battle.py) | 0 | Просмотрено, без правки |
| [src/mmorpg/application/services/character_reset.py](../src/mmorpg/application/services/character_reset.py) | 9 | Просмотрено, без правки |
| [src/mmorpg/application/services/city_event.py](../src/mmorpg/application/services/city_event.py) | 23 | Текст изменён |
| [src/mmorpg/application/services/content.py](../src/mmorpg/application/services/content.py) | 0 | Просмотрено, без правки |
| [src/mmorpg/application/services/group_trade.py](../src/mmorpg/application/services/group_trade.py) | 1 | Просмотрено, без правки |
| [src/mmorpg/application/services/guild.py](../src/mmorpg/application/services/guild.py) | 0 | Просмотрено, без правки |
| [src/mmorpg/application/services/guild_safety.py](../src/mmorpg/application/services/guild_safety.py) | 5 | Просмотрено, без правки |
| [src/mmorpg/application/services/health_recovery.py](../src/mmorpg/application/services/health_recovery.py) | 0 | Просмотрено, без правки |
| [src/mmorpg/application/services/invitations.py](../src/mmorpg/application/services/invitations.py) | 9 | Просмотрено, без правки |
| [src/mmorpg/application/services/keeper.py](../src/mmorpg/application/services/keeper.py) | 0 | Просмотрено, без правки |
| [src/mmorpg/application/services/keeper_panel.py](../src/mmorpg/application/services/keeper_panel.py) | 0 | Просмотрено, без правки |
| [src/mmorpg/application/services/long_goal.py](../src/mmorpg/application/services/long_goal.py) | 32 | Текст изменён |
| [src/mmorpg/application/services/market.py](../src/mmorpg/application/services/market.py) | 43 | Просмотрено, без правки |
| [src/mmorpg/application/services/moderation.py](../src/mmorpg/application/services/moderation.py) | 0 | Просмотрено, без правки |
| [src/mmorpg/application/services/party.py](../src/mmorpg/application/services/party.py) | 0 | Просмотрено, без правки |
| [src/mmorpg/application/services/recruitment.py](../src/mmorpg/application/services/recruitment.py) | 27 | Просмотрено, без правки |
| [src/mmorpg/application/services/war_score.py](../src/mmorpg/application/services/war_score.py) | 8 | Просмотрено, без правки |
| [src/mmorpg/presentation/telegram/flows/__init__.py](../src/mmorpg/presentation/telegram/flows/__init__.py) | 0 | Просмотрено, без правки |
| [src/mmorpg/presentation/telegram/flows/combat.py](../src/mmorpg/presentation/telegram/flows/combat.py) | 5 | Просмотрено, без правки |
| [src/mmorpg/presentation/telegram/flows/creation.py](../src/mmorpg/presentation/telegram/flows/creation.py) | 41 | Просмотрено, без правки |
| [src/mmorpg/presentation/telegram/flows/keeper.py](../src/mmorpg/presentation/telegram/flows/keeper.py) | 252 | Просмотрено, без правки |
| [src/mmorpg/presentation/telegram/flows/play.py](../src/mmorpg/presentation/telegram/flows/play.py) | 296 | Текст изменён |
| [src/mmorpg/presentation/telegram/flows/state.py](../src/mmorpg/presentation/telegram/flows/state.py) | 0 | Просмотрено, без правки |
| [src/mmorpg/presentation/telegram/handlers/__init__.py](../src/mmorpg/presentation/telegram/handlers/__init__.py) | 0 | Просмотрено, без правки |
| [src/mmorpg/presentation/telegram/handlers/character_reset.py](../src/mmorpg/presentation/telegram/handlers/character_reset.py) | 36 | Просмотрено, без правки |
| [src/mmorpg/presentation/telegram/handlers/city_event.py](../src/mmorpg/presentation/telegram/handlers/city_event.py) | 22 | Просмотрено, без правки |
| [src/mmorpg/presentation/telegram/handlers/combat.py](../src/mmorpg/presentation/telegram/handlers/combat.py) | 64 | Просмотрено, без правки |
| [src/mmorpg/presentation/telegram/handlers/creation.py](../src/mmorpg/presentation/telegram/handlers/creation.py) | 12 | Просмотрено, без правки |
| [src/mmorpg/presentation/telegram/handlers/group.py](../src/mmorpg/presentation/telegram/handlers/group.py) | 3 | Просмотрено, без правки |
| [src/mmorpg/presentation/telegram/handlers/long_goal.py](../src/mmorpg/presentation/telegram/handlers/long_goal.py) | 48 | Просмотрено, без правки |
| [src/mmorpg/presentation/telegram/handlers/market.py](../src/mmorpg/presentation/telegram/handlers/market.py) | 172 | Просмотрено, без правки |
| [src/mmorpg/presentation/telegram/handlers/play.py](../src/mmorpg/presentation/telegram/handlers/play.py) | 199 | Просмотрено, без правки |
| [src/mmorpg/presentation/telegram/handlers/recruitment.py](../src/mmorpg/presentation/telegram/handlers/recruitment.py) | 69 | Просмотрено, без правки |
| [src/mmorpg/presentation/telegram/screens/__init__.py](../src/mmorpg/presentation/telegram/screens/__init__.py) | 0 | Просмотрено, без правки |
| [src/mmorpg/presentation/telegram/screens/arena.py](../src/mmorpg/presentation/telegram/screens/arena.py) | 24 | Просмотрено, без правки |
| [src/mmorpg/presentation/telegram/screens/base.py](../src/mmorpg/presentation/telegram/screens/base.py) | 0 | Просмотрено, без правки |
| [src/mmorpg/presentation/telegram/screens/city.py](../src/mmorpg/presentation/telegram/screens/city.py) | 85 | Текст изменён |
| [src/mmorpg/presentation/telegram/screens/city_event.py](../src/mmorpg/presentation/telegram/screens/city_event.py) | 30 | Просмотрено, без правки |
| [src/mmorpg/presentation/telegram/screens/crafts.py](../src/mmorpg/presentation/telegram/screens/crafts.py) | 50 | Просмотрено, без правки |
| [src/mmorpg/presentation/telegram/screens/creation.py](../src/mmorpg/presentation/telegram/screens/creation.py) | 100 | Текст изменён |
| [src/mmorpg/presentation/telegram/screens/dungeon.py](../src/mmorpg/presentation/telegram/screens/dungeon.py) | 24 | Текст изменён |
| [src/mmorpg/presentation/telegram/screens/format.py](../src/mmorpg/presentation/telegram/screens/format.py) | 28 | Просмотрено, без правки |
| [src/mmorpg/presentation/telegram/screens/group.py](../src/mmorpg/presentation/telegram/screens/group.py) | 66 | Просмотрено, без правки |
| [src/mmorpg/presentation/telegram/screens/guild.py](../src/mmorpg/presentation/telegram/screens/guild.py) | 161 | Просмотрено, без правки |
| [src/mmorpg/presentation/telegram/screens/house.py](../src/mmorpg/presentation/telegram/screens/house.py) | 13 | Просмотрено, без правки |
| [src/mmorpg/presentation/telegram/screens/journey.py](../src/mmorpg/presentation/telegram/screens/journey.py) | 38 | Просмотрено, без правки |
| [src/mmorpg/presentation/telegram/screens/keeper.py](../src/mmorpg/presentation/telegram/screens/keeper.py) | 316 | Просмотрено, без правки |
| [src/mmorpg/presentation/telegram/screens/long_goal.py](../src/mmorpg/presentation/telegram/screens/long_goal.py) | 70 | Просмотрено, без правки |
| [src/mmorpg/presentation/telegram/screens/moderation.py](../src/mmorpg/presentation/telegram/screens/moderation.py) | 7 | Просмотрено, без правки |
| [src/mmorpg/presentation/telegram/screens/paginated.py](../src/mmorpg/presentation/telegram/screens/paginated.py) | 23 | Просмотрено, без правки |
| [src/mmorpg/presentation/telegram/screens/party.py](../src/mmorpg/presentation/telegram/screens/party.py) | 21 | Текст изменён |
| [src/mmorpg/presentation/telegram/screens/play.py](../src/mmorpg/presentation/telegram/screens/play.py) | 221 | Текст изменён |
| [src/mmorpg/presentation/telegram/screens/quests.py](../src/mmorpg/presentation/telegram/screens/quests.py) | 65 | Текст изменён |
| [src/mmorpg/presentation/telegram/screens/recruitment.py](../src/mmorpg/presentation/telegram/screens/recruitment.py) | 46 | Просмотрено, без правки |
| [src/mmorpg/presentation/telegram/screens/settings.py](../src/mmorpg/presentation/telegram/screens/settings.py) | 15 | Просмотрено, без правки |
| [src/mmorpg/presentation/telegram/screens/shop.py](../src/mmorpg/presentation/telegram/screens/shop.py) | 33 | Просмотрено, без правки |
| [src/mmorpg/presentation/telegram/screens/subclass.py](../src/mmorpg/presentation/telegram/screens/subclass.py) | 33 | Просмотрено, без правки |
| [src/mmorpg/presentation/telegram/screens/talents.py](../src/mmorpg/presentation/telegram/screens/talents.py) | 80 | Просмотрено, без правки |
| [src/mmorpg/presentation/telegram/screens/transfer.py](../src/mmorpg/presentation/telegram/screens/transfer.py) | 20 | Просмотрено, без правки |
| [src/mmorpg/presentation/telegram/screens/tutorial.py](../src/mmorpg/presentation/telegram/screens/tutorial.py) | 41 | Текст изменён |
