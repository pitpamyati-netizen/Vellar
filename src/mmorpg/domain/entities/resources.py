"""Два классовых ресурса и совместимые названия прежних боёв."""

_KINDS = {
    "valor": "stamina",
    "rage": "stamina",
    "focus": "stamina",
    "energy": "stamina",
    "grace": "stamina",
    "faith": "mana",
    "communion": "mana",
}
_NAMES = {
    "Отвага": "Выносливость",
    "Ярость": "Выносливость",
    "Фокус": "Выносливость",
    "Выдержка": "Выносливость",
    "Прыть": "Выносливость",
    "Благодать": "Выносливость",
    "Чары": "Мана",
    "Вера": "Мана",
    "Единение": "Мана",
}


def resource_name(name: str) -> str:
    return _NAMES.get(name, name)


def resource_kind(kind: str, name: str) -> tuple[str, str]:
    kind = _KINDS.get(kind, kind)
    return kind, {"stamina": "Выносливость", "mana": "Мана"}.get(kind, name)
