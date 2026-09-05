"""Помощники для записи текста экранов.

Всё здесь держится на двух правилах (``docs/accessibility.md``):

- **никакой псевдографики** — никогда ``[####----]``, всегда «42 из 120,
  35 процентов»;
- **главное первым** — строки складывает вызывающий, а эти помощники держат
  каждую короткой и произносимой.
"""

from __future__ import annotations

#: Сколько знаков экран кладёт в одно сообщение. Держит
#: ``tests/presentation/test_accessibility.py``.
MESSAGE_LIMIT = 2000

#: Предел самого Telegram: последняя преграда отправки, до которой экраны не доходят.
HARD_LIMIT = 4096


def head(title: str, notice: str = "") -> tuple[str, ...]:
    """Заголовок экрана, а перед ним — весть о том, что только что случилось.

    Весть отвечает «что случилось», заголовок - «где я», и это два разных вопроса
    (``docs/accessibility.md``, правило 4). Экран, писавший ``notice or
    "Характеристики."``, на всяком действии съедал собственное название.
    """
    return (notice, title) if notice else (title,)


def amount(current: int, maximum: int, *, with_percent: bool = True) -> str:
    """Показать значение, похожее на полосу, речью: ``42 из 120, 35 процентов``."""
    if maximum <= 0:
        return f"{current}"
    if not with_percent:
        return f"{current} из {maximum}"
    percent = round(current * 100 / maximum)
    return f"{current} из {maximum}, {percent} процентов"


def number(value: float) -> str:
    """Число так, как его читают вслух: без хвостового нуля, дробь через запятую.

    Точка в дроби на слух — «две точка семь пять», и это не число, а диктовка.
    """
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")


def percent(value: float) -> str:
    """Проценты словами, с явным «минус» у штрафа.

    Слово согласуется с числом: «5 процентов», «2 процента», «2,75 процента».
    """
    rounded = round(value, 2)
    magnitude = abs(rounded)
    # У дробного числа слово всегда в родительном единственном: «2,75 процента».
    word = plural(int(magnitude), "процент", "процента", "процентов")
    if magnitude != int(magnitude):
        word = "процента"
    body = f"{number(magnitude)} {word}"
    return f"минус {body}" if rounded < 0 else body


def plural(count: int, one: str, few: str, many: str) -> str:
    """Russian pluralisation: 1 ход, 2 хода, 5 ходов."""
    tail_two = abs(count) % 100
    tail = abs(count) % 10
    if 11 <= tail_two <= 14:
        return many
    if tail == 1:
        return one
    if 2 <= tail <= 4:
        return few
    return many


def turns(count: int) -> str:
    return f"{count} {plural(count, 'ход', 'хода', 'ходов')}"


def items(count: int) -> str:
    return f"{count} {plural(count, 'предмет', 'предмета', 'предметов')}"


def found(count: int) -> str:
    return f"Найдено {count} {plural(count, 'запись', 'записи', 'записей')}"


def duration(seconds: int) -> str:
    """Срок словами, самой крупной мерой, какая помещается.

    Точность здесь не нужна и вредна: заблокированному важно «ещё двое суток», а
    не «двое суток три часа одиннадцать минут». Меньше минуты не бывает: ноль
    сказать нечем.
    """
    if seconds >= 24 * 60 * 60:
        days = seconds // (24 * 60 * 60)
        return f"{days} {plural(days, 'сутки', 'суток', 'суток')}"
    if seconds >= 60 * 60:
        hours = seconds // (60 * 60)
        return f"{hours} {plural(hours, 'час', 'часа', 'часов')}"
    minutes = max(1, seconds // 60)
    return f"{minutes} {plural(minutes, 'минута', 'минуты', 'минут')}"


def gold(value: int) -> str:
    return f"{value} {plural(value, 'золотой', 'золотых', 'золотых')}"


def paginate_text(text: str, limit: int = MESSAGE_LIMIT) -> tuple[str, ...]:
    """Разрезать слишком длинное сообщение по границам строк.

    Берётся только для того содержимого, которое и правда не сократить; от экранов
    ждут, что они порежут свои данные на страницы (правило доступности 11).
    """
    if len(text) <= limit:
        return (text,)

    pages: list[str] = []
    current: list[str] = []
    length = 0
    for line in text.split("\n"):
        addition = len(line) + 1
        if length + addition > limit and current:
            pages.append("\n".join(current))
            current, length = [], 0
        current.append(line)
        length += addition
    if current:
        pages.append("\n".join(current))
    return tuple(pages)


def join_lines(*lines: str) -> str:
    """Выбросить пустые записи и сшить одиночными переводами строк."""
    return "\n".join(line for line in lines if line)
