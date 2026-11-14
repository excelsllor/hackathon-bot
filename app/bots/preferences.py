from __future__ import annotations

FORMAT_LABELS = {
    "any": "любой",
    "online": "онлайн",
    "offline": "офлайн",
}

SCOPE_LABELS = {
    "both": "РФ + мир",
    "ru": "только РФ",
    "intl": "только мир",
}


def format_label(value: str) -> str:
    return FORMAT_LABELS.get(value, value)


def scope_label(value: str) -> str:
    return SCOPE_LABELS.get(value, value)


def changed_preference_text(key: str, value: str) -> str | None:
    if key == "format":
        return f"Формат: {format_label(value)}"
    if key == "scope":
        return f"География: {scope_label(value)}"
    if key == "digest":
        return "Дайджест: включён" if value == "on" else "Дайджест: выключен"
    return None
