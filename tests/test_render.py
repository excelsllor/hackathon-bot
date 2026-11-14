from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.bots.render import build_digest_chunks, build_pages, item_card
from app.config import Settings
from app.models import Item, dump_list
from app.pipeline.classify import load_topics

CONFIG = load_topics(Path(__file__).resolve().parents[1] / "config" / "topics.yaml")
SETTINGS = Settings()


def make_item(item_id: int, title: str = "AI Hackathon", topic: str = "ai") -> Item:
    now = datetime.now(timezone.utc)
    return Item(
        id=item_id,
        canonical_url=f"https://example.com/{item_id}",
        source_code="test",
        title=title,
        description="Описание хакатона",
        url=f"https://example.com/{item_id}",
        starts_at=now + timedelta(days=3),
        ends_at=now + timedelta(days=10),
        reg_deadline=now + timedelta(days=5),
        date_confidence="approx",
        format="online",
        geo="intl",
        language="ru",
        eligibility="worldwide",
        topics_json=dump_list([topic]),
        tags_json=dump_list(["AI"]),
        matched_json="[]",
        extra_sources_json=dump_list(["test"]),
        first_seen_at=now,
        last_seen_at=now,
        is_active=True,
    )


def test_item_card_escapes_html():
    item = make_item(1, "<script>alert(1)</script> AI Hackathon")
    card = item_card(item, CONFIG, SETTINGS, html_mode=True)
    assert "<script>" not in card
    assert "&lt;script&gt;" in card
    assert "Открыть" in card


def test_build_pages_splits_by_page_size():
    items = [make_item(i) for i in range(1, 8)]
    pages = build_pages([("ai", items)], CONFIG, SETTINGS, html_mode=True)
    assert len(pages) == 2
    assert len(pages[0].items) == 5
    assert len(pages[1].items) == 2


def test_build_pages_all_mode_packs_more_than_default():
    items = [make_item(i) for i in range(1, 8)]
    pages = build_pages(
        [("ai", items)], CONFIG, SETTINGS, html_mode=True, page_size=50
    )
    assert len(pages) == 1
    assert len(pages[0].items) == 7


def test_digest_chunks_not_empty():
    items = [make_item(i) for i in range(1, 4)]
    chunks = build_digest_chunks([("ai", items)], CONFIG, SETTINGS)
    assert chunks
    assert "AI Hackathon" in chunks[0]
