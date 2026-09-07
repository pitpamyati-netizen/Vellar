"""Имя, которым игрока зовут вслух: что игра принимает и почему отказывает.

Имя персонажа слышат все. Оно стоит в очереди боя, в составе отряда, в списке
гильдии и в сводке заставы, и произносит его экранный диктор, а не читает глаз.
Поэтому проверок пять, и каждая отвечает целой фразой, а не «недопустимое имя».

1. **Длина, набор знаков и один алфавит.** Буквы, цифры, пробел, дефис и
   апостроф; кириллица и латиница вперемешку - это не имя, а подделка под
   чужое: «Аргус» с латинской «A» читается одинаково, а зовётся по-разному.
   Цифр не больше двух: число вслух звучит как число.
2. **Брань.** Ищется по свёрнутому виду, а не по написанному: латиница и цифры
   складываются в кириллицу, повторы схлопываются, знаки разделения выпадают.
   «ПИZDа», «хууй» и «х у й» ловятся тем же корнем, что и написанное прямо.
3. **Имя настоящего человека.** Приключенец - не Саша, не Иванов и не
   Николаевич: паспортное имя в мире не звучит (ADR 0078).
4. **Бессмысленный набор букв.** «фыва», «asdfgh», «Кфцщ» - это не имя, а
   нажатое наугад: вслух оно не читается ничем.

Модуль чистый - ни времени, ни случайности, ни ввода-вывода, - и зовут его все
трое, кто принимает набранное имя: создание персонажа, правка смотрителя и
грамота гильдии.
"""

from __future__ import annotations

import re

MIN_LENGTH = 2
MAX_LENGTH = 20

#: Цифр в имени не больше этого: «Игрок 2» ещё имя, «Игрок123» - уже прозвище.
MAX_DIGITS = 2

#: Столько одинаковых букв подряд («Ааарон») уже не читаются.
MAX_REPEATS = 3
#: Столько согласных подряд («Штрбск») не выговаривает никто.
MAX_CONSONANTS = 5
#: Столько подряд с одного ряда клавиатуры («qwert») - это не набранное имя.
KEYBOARD_RUN = 5
#: Слово, сложенное из таких кусков от начала до конца, набрано, а не придумано.
KEYBOARD_PIECE = 3
#: Короче этого слово рядами не меряют: «мит» есть и в ряду, и в имени.
KEYBOARD_WORD = 4

#: Имя, раскиданное по буквам, склеивают обратно: слова короче этого - не слова.
_SCATTERED = 2

_ALLOWED = re.compile(r"^[А-Яа-яЁёA-Za-z][А-Яа-яЁёA-Za-z0-9 \-']*$")
_SPLIT = re.compile(r"[ \-']+")
_CYRILLIC = re.compile(r"[А-Яа-яЁё]")
_LATIN = re.compile(r"[A-Za-z]")

# Латиница и цифры -> кириллические буквы (леетсфик / гомоглифы). Карта
# визуальных двойников плюс частые цифровые замены: латинская D -> д, n -> п,
# l -> л, чтобы «ПИЗDа» и «eбalo» читались как написанные кириллицей.
_LEET = {
    "0": "о", "1": "л", "2": "з", "3": "е", "4": "а", "5": "с",
    "6": "б", "7": "т", "8": "ь", "9": "г",
    "a": "а", "b": "ь", "c": "с", "d": "д", "e": "е", "h": "х",
    "i": "и", "k": "к", "l": "л", "m": "м", "n": "п", "o": "о",
    "p": "р", "t": "т", "u": "у", "w": "ш", "x": "х", "y": "у",
    "z": "з",
}  # fmt: skip

# Цифра читается двояко: «4» - это и латинская «a», и русская «ч», «3» - и «e»,
# и «з». Свёртка поэтому не одна: брань ищут по обеим, и «Су4ка» ловится так же,
# как «Cyка».
_LEET_RU = _LEET | {"4": "ч", "3": "з"}

#: Готовые таблицы свёртки: обе читают одно и то же слово по-своему.
_TABLES = (str.maketrans(_LEET), str.maketrans(_LEET_RU))

# Запрещённые корни (после свёртки в кириллицу). Корни подобраны так, чтобы не
# цеплять безобидные слова: длиннее двух-трёх букв, без «еб» в одиночку - оно
# стоит и в «Небесах», и в «серебряном». «Ё» здесь пишут как слышат, а
# сравнивают её с «е»: слова разбираются без «ё», и «Ёбарь» ловится на «еба».
_BANNED_RU = frozenset(
    root.replace("ё", "е")
    for root in {
        "хуй", "хуё", "хуе", "хуя", "хуи", "хую",
        "пизд", "пзд",
        "бля",
        "выеб", "заеб", "наеб", "поеб", "приеб", "доеб", "объеб", "разъеб",
        "долбоеб",
        "мудак", "мудил", "мудло",
        "гандон", "гондон",
        "залуп",
        "манда",
        "шлюх",
        "сука", "сучк", "сучар",
        "пидор", "пидар", "педик", "пидрас",
        "ублюд", "уеб", "ебок",
        "хер", "хрен",
        "срать", "срач", "срёш",
        "чмо", "чмыр",
        "мразь", "мрази",
        "тварь", "твари",
        "говн", "дерьм",
        "гнид", "поган", "выбляд",
        "жоп",
        "козёл", "козел",
        "жид", "хач", "чурк", "ниггер", "черномаз", "пиндос",
    }
)  # fmt: skip

# Корни, которые ищут только с начала слова. «Еба» посреди слова стоит в
# «Требухе», «Ребусе» и «Учёбе», и корень, ловящий их, ловит не брань, а букву.
# С начала же слова оно ровно то, чем кажется, а с приставкой ловится списком
# выше.
_BANNED_START = frozenset(
    root.replace("ё", "е")
    for root in {
        "ебать", "ебат", "ебал", "еба", "ебла", "ебло", "еби", "ебу",
        "ебну", "ебан", "ебок", "ёб",
    }
)  # fmt: skip

# Английская брань и латинские транслитерации мата: их ищут по несвёрнутому
# виду, потому что свёртка увела бы латиницу в кириллицу.
_BANNED_EN = frozenset(
    {
        "fuck", "shit", "bitch", "asshole", "dick", "cunt", "nigger",
        "faggot", "motherfucker", "whore", "slut", "bastard",
        "pizda", "hui", "pidor", "pidar", "blyad", "blyat", "govno", "suka",
    }
)  # fmt: skip

# Настоящие имена: полные и уменьшительные. Ловится слово целиком, а не корень,
# иначе «Иванна» и «Романия» уехали бы вслед за «Иваном» и «Романом».
_RU_GIVEN = frozenset(
    {
        "александр", "александра", "алексей", "алина", "алиса", "алла",
        "анастасия", "ангелина", "андрей", "анна", "антон", "артем", "артур",
        "борис", "вадим", "валентин", "валентина", "валерий", "валерия",
        "василий", "вера", "вероника", "виктор", "виктория", "виталий",
        "владимир", "владислав", "вячеслав", "галина", "геннадий", "георгий",
        "григорий", "дарья", "даниил", "данил", "денис", "диана", "дмитрий",
        "евгений", "евгения", "егор", "екатерина", "елена", "елизавета",
        "иван", "игорь", "илья", "инна", "ирина", "карина", "кирилл",
        "константин", "кристина", "ксения", "лариса", "лев", "леонид", "лидия",
        "любовь", "людмила", "макар", "максим", "маргарита", "марина", "мария",
        "марк", "матвей", "михаил", "надежда", "наталья", "наталия", "никита",
        "николай", "нина", "оксана", "олег", "олеся", "ольга", "павел",
        "петр", "полина", "роман", "руслан", "светлана", "семен", "сергей",
        "софия", "софья", "станислав", "степан", "тамара", "татьяна", "тимофей",
        "тимур", "ульяна", "федор", "юлия", "юрий", "яна", "ярослав",
    }
)  # fmt: skip

_RU_SHORT = frozenset(
    {
        "аня", "вова", "влад", "ваня", "вика", "дима", "даша", "женя", "ира",
        "катя", "коля", "костя", "ксюша", "лена", "леша", "лиза", "люда",
        "маша", "миша", "митя", "надя", "настя", "наташа", "оля", "паша",
        "петя", "рита", "рома", "саня", "саша", "света", "серега", "сережа",
        "соня", "стас", "таня", "толя", "юля", "юра",
    }
)  # fmt: skip

# Двадцать самых частых фамилий. Правило суффикса тут не годится: «Волков» и
# «Драконов» кончаются одинаково, а живёт из них по-настоящему только первый.
_RU_SURNAMES = frozenset(
    {
        "алексеев", "васильев", "волков", "егоров", "иванов", "козлов",
        "кузнецов", "лебедев", "макаров", "михайлов", "морозов", "новиков",
        "павлов", "петров", "попов", "семенов", "сидоров", "смирнов", "смит",
        "соколов", "степанов", "федоров",
    }
)  # fmt: skip

# Отчество узнаётся по хвосту: «-ович» и «-евна» не бывают выдумкой.
_PATRONYMICS = ("ович", "евич", "овна", "евна", "ична")

_EN_GIVEN = frozenset(
    {
        "adam", "alan", "albert", "alex", "alice", "amanda", "amy", "andrew",
        "angela", "ann", "anna", "anthony", "ashley", "barbara", "ben",
        "benjamin", "betty", "bill", "bob", "brandon", "brian", "carl", "carol",
        "charles", "chris", "christina", "christopher", "daniel", "dave",
        "david", "deborah", "dennis", "diana", "donald", "donna", "dorothy",
        "edward", "elizabeth", "emily", "emma", "eric", "frank", "george",
        "gerald", "grace", "gregory", "hannah", "harry", "helen", "henry",
        "jack", "jacob", "james", "jane", "janet", "jason", "jeffrey",
        "jennifer", "jerry", "jessica", "joan", "joe", "john", "jose", "joseph",
        "joshua", "juan", "judith", "julia", "julie", "justin", "karen",
        "katherine", "kathleen", "keith", "kelly", "kenneth", "kevin",
        "kimberly", "larry", "laura", "lauren", "linda", "lisa", "logan",
        "louis", "maria", "mark", "martha", "mary", "matthew", "megan",
        "melissa", "michael", "michelle", "mike", "nancy", "nathan", "nicholas",
        "nicole", "noah", "olivia", "pamela", "patricia", "patrick", "paul",
        "peter", "philip", "rachel", "ralph", "raymond", "rebecca", "richard",
        "robert", "roger", "ronald", "roy", "ruth", "ryan", "samantha",
        "samuel", "sandra", "sarah", "scott", "sean", "sharon", "shirley",
        "sophia", "stephanie", "stephen", "steven", "susan", "teresa", "terry",
        "thomas", "timothy", "tom", "tyler", "victoria", "vincent", "walter",
        "wayne", "william", "zachary",
    }
)  # fmt: skip

# Самые частые английские фамилии: «Smith» ловится и как фамилия, и как «смит».
_EN_SURNAMES = frozenset(
    {
        "anderson", "brown", "clark", "davis", "garcia", "harris", "jackson",
        "johnson", "jones", "lee", "lewis", "martin", "martinez", "miller",
        "moore", "robinson", "rodriguez", "smith", "taylor", "thompson",
        "walker", "white", "williams", "wilson", "young",
    }
)  # fmt: skip

# Русское имя, написанное латиницей, - то же имя: «Sasha» и «Ivanovich» читаются
# вслух ровно так, как написаны кириллицей. Двузначные сочетания идут первыми,
# иначе «sh» разберётся по буквам.
_TRANSLIT: tuple[tuple[str, str], ...] = (
    ("shch", "щ"), ("sch", "щ"), ("sh", "ш"), ("ch", "ч"), ("zh", "ж"),
    ("kh", "х"), ("ts", "ц"), ("ya", "я"), ("yu", "ю"), ("yo", "е"),
    ("ye", "е"), ("iy", "ий"), ("a", "а"), ("b", "б"), ("c", "к"), ("d", "д"),
    ("e", "е"), ("f", "ф"), ("g", "г"), ("h", "х"), ("i", "и"), ("j", "й"),
    ("k", "к"), ("l", "л"), ("m", "м"), ("n", "н"), ("o", "о"), ("p", "п"),
    ("q", "к"), ("r", "р"), ("s", "с"), ("t", "т"), ("u", "у"), ("v", "в"),
    ("w", "в"), ("x", "кс"), ("y", "ы"), ("z", "з"),
)  # fmt: skip

_REAL_NAMES = frozenset(
    name.replace("ё", "е")
    for name in (_RU_GIVEN | _RU_SHORT | _RU_SURNAMES | _EN_GIVEN | _EN_SURNAMES)
)

#: Гласные обоих алфавитов. Слово без единой - не слово («кфцщ»).
_VOWELS = frozenset("аеиоуыэюяaeiouy")

# Ряды клавиатуры обоих раскладок. Цифрового ряда тут нет нарочно: цифр в имени
# и так не больше двух, и набрать ими ряд не выйдет.
_ROWS = (
    "йцукенгшщзхъ",
    "фывапролджэ",
    "ячсмитьбю",
    "qwertyuiop",
    "asdfghjkl",
    "zxcvbnm",
)


def _words(name: str) -> tuple[str, ...]:
    """Слова имени в нижнем регистре, без «ё» и без знаков разделения."""
    lowered = name.strip().lower().replace("ё", "е")
    return tuple(word for word in _SPLIT.split(lowered) if word)


def _squash(text: str, *, table: dict[int, str] | None) -> str:
    """Свёрнутый вид: только буквы, повторы схлопнуты, при ``table`` - в кириллицу."""
    lowered = text.lower()
    if table is not None:
        lowered = lowered.translate(table)
    squashed: list[str] = []
    for char in lowered:
        if not char.isalpha():
            continue
        if not squashed or squashed[-1] != char:
            squashed.append(char)
    return "".join(squashed)


def _profane_chunk(chunk: str) -> bool:
    for table in _TABLES:
        squashed = _squash(chunk, table=table)
        if any(root in squashed for root in _BANNED_RU):
            return True
        if squashed.startswith(tuple(_BANNED_START)):
            return True
    return any(root in _squash(chunk, table=None) for root in _BANNED_EN)


def is_profane(name: str) -> bool:
    """Есть ли в имени брань - в том числе леетсфиком и вразбивку."""
    words = _words(name)
    if not words:
        return False
    if any(_profane_chunk(word) for word in words):
        return True
    # Слова целиком склеивают только тогда, когда все они - обрывки: «х у й»
    # склеить надо, «Топ Издалека» - ни в коем случае.
    if all(len(word) <= _SCATTERED for word in words):
        return _profane_chunk("".join(words))
    return False


def _transliterated(word: str) -> str:
    """Латиница, прочитанная кириллицей: «sasha» - это «саша»."""
    if not _LATIN.search(word):
        return word
    read = word
    for latin, cyrillic in _TRANSLIT:
        read = read.replace(latin, cyrillic)
    return read


def is_real_name(name: str) -> bool:
    """Стоит ли в имени слово из паспорта - имя, фамилия или отчество."""
    for word in _words(name):
        for read in (word, _transliterated(word)):
            if read in _REAL_NAMES:
                return True
            if len(read) > 5 and read.endswith(_PATRONYMICS):
                return True
    return False


def _longest_run(letters: str, *, same: bool) -> int:
    """Самая длинная цепочка подряд: одинаковых букв или согласных."""
    longest = 0
    run = 0
    previous = ""
    for char in letters:
        if same:
            run = run + 1 if char == previous else 1
        elif char not in _VOWELS:
            run += 1
        else:
            run = 0
        previous = char
        longest = max(longest, run)
    return longest


def _in_row(piece: str) -> bool:
    return any(piece in row or piece in row[::-1] for row in _ROWS)


def _row_run(word: str) -> bool:
    """Есть ли в слове длинный кусок, набранный подряд по ряду клавиатуры."""
    return any(
        _in_row(word[start : start + KEYBOARD_RUN]) for start in range(len(word) - KEYBOARD_RUN + 1)
    )


def _typed_out(word: str) -> bool:
    """Сложено ли слово из рядов клавиатуры целиком, от начала до конца.

    Куском короче ``KEYBOARD_PIECE`` слово не набирают: «мить» лежит и в третьем
    ряду, и в «Митьке», и разница ровно в том, чем набран остаток.
    """
    if len(word) < KEYBOARD_WORD:
        return False
    reached = [False] * (len(word) + 1)
    reached[0] = True
    for end in range(KEYBOARD_PIECE, len(word) + 1):
        reached[end] = any(
            reached[start] and _in_row(word[start:end]) for start in range(end - KEYBOARD_PIECE + 1)
        )
    return reached[-1]


def is_gibberish(name: str) -> bool:
    """Не читается ли имя вслух: без гласных, с повторами или набранное рядом."""
    for word in _words(name):
        letters = "".join(char for char in word if char.isalpha())
        if len(letters) < 3:
            continue
        if not any(char in _VOWELS for char in letters):
            return True
        if _longest_run(letters, same=True) >= MAX_REPEATS:
            return True
        if _longest_run(letters, same=False) >= MAX_CONSONANTS:
            return True
        if _row_run(letters) or _typed_out(letters):
            return True
    return False


def refusal(name: str) -> str:
    """Пусто, когда имя годится; иначе - целой фразой, чем оно не годится."""
    trimmed = name.strip()
    if len(trimmed) < MIN_LENGTH:
        return f"Имя должно быть не короче {MIN_LENGTH} символов."
    if len(trimmed) > MAX_LENGTH:
        return f"Имя должно быть не длиннее {MAX_LENGTH} символов."
    if not _ALLOWED.match(trimmed):
        return (
            "Имя может содержать буквы, цифры, пробел, дефис и апостроф, "
            "и должно начинаться с буквы."
        )
    if _CYRILLIC.search(trimmed) and _LATIN.search(trimmed):
        return "Имя пишут русскими буквами или латинскими, но не вперемешку."
    if sum(char.isdigit() for char in trimmed) > MAX_DIGITS:
        return f"Цифр в имени - не больше {MAX_DIGITS}: имя произносят вслух."
    if is_profane(trimmed):
        return "Такое имя игра не примет. Придумайте другое."
    if is_real_name(trimmed):
        return "Это имя настоящего человека. Приключенцу нужно своё."
    if is_gibberish(trimmed):
        return "Имя должно читаться вслух. Наберите то, что можно произнести."
    return ""
