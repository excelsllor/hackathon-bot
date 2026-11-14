from pathlib import Path

from app.pipeline.classify import classify_text, load_topics

CONFIG = load_topics(Path(__file__).resolve().parents[1] / "config" / "topics.yaml")


def test_ai_hackathon_from_devpost():
    topics, tags, _ = classify_text(
        "PayPal AI Hackathon",
        "Организатор: PayPal. Темы: Machine Learning/AI, Web.",
        ["Machine Learning/AI", "Web"],
        CONFIG,
        trusted_event=True,
    )
    assert "ai" in topics
    assert "web" in topics
    assert "it" not in topics
    assert "Machine Learning/AI" in tags


def test_russian_web_hackathon():
    topics, _, _ = classify_text(
        "Хакатон по веб-разработке",
        "Сделаем сервис на Python и React.",
        [],
        CONFIG,
        trusted_event=False,
    )
    assert "web" in topics
    assert "it" not in topics


def test_hr_hackathon_is_filtered_out():
    topics, _, _ = classify_text(
        "HR-хакатон",
        "Ищем HR-специалистов и рекрутеров.",
        [],
        CONFIG,
        trusted_event=False,
    )
    assert topics == []


def test_game_jam():
    topics, _, _ = classify_text(
        "Game Jam 2026",
        "Создай игру на Unity за выходные.",
        [],
        CONFIG,
        trusted_event=False,
    )
    assert "game" in topics


def test_trusted_default_topic():
    topics, _, _ = classify_text(
        "Some Competition",
        "A generic event",
        [],
        CONFIG,
        trusted_event=True,
        default_topic="ds",
    )
    assert topics == ["ds"]


def test_ai_and_it_not_duplicated():
    topics, _, _ = classify_text(
        "AI hackathon",
        "Build something with LLM",
        [],
        CONFIG,
        trusted_event=False,
    )
    assert "ai" in topics
    assert "it" not in topics


def test_tag_mapping():
    topics, _, _ = classify_text(
        "Build with us",
        "Event for developers",
        ["Blockchain"],
        CONFIG,
        trusted_event=True,
    )
    assert "chain" in topics
