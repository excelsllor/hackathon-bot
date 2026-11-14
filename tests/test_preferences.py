from app.bots.preferences import (
    changed_preference_text,
    format_label,
    scope_label,
)


def test_format_labels():
    assert format_label("any") == "любой"
    assert format_label("online") == "онлайн"
    assert format_label("offline") == "офлайн"


def test_scope_labels():
    assert scope_label("both") == "РФ + мир"
    assert scope_label("ru") == "только РФ"
    assert scope_label("intl") == "только мир"


def test_changed_preference_text():
    assert changed_preference_text("format", "online") == "Формат: онлайн"
    assert changed_preference_text("scope", "ru") == "География: только РФ"
    assert changed_preference_text("strict", "1") is None
    assert changed_preference_text("digest", "on") == "Дайджест: включён"
    assert changed_preference_text("digest", "off") == "Дайджест: выключен"
    assert changed_preference_text("time", "10:00") is None
