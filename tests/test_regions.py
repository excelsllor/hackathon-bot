from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.bots.render import build_pages, group_by_region
from app.config import Settings
from app.models import Item, dump_list
from app.pipeline.classify import load_topics
from app.pipeline.regions import (
    classify_region,
    effective_region,
    region_allowed,
)

TOPICS = load_topics(Path(__file__).resolve().parents[1] / "config" / "topics.yaml")
NOW = datetime.now(timezone.utc)


def make_item(item_id: int, region: str, geo: str = "ru") -> Item:
    return Item(
        id=item_id,
        canonical_url=f"https://example.com/{item_id}",
        source_code="test",
        title=f"Event {item_id}",
        description="",
        url=f"https://example.com/{item_id}",
        starts_at=NOW + timedelta(days=3),
        ends_at=NOW + timedelta(days=5),
        reg_deadline=NOW + timedelta(days=2),
        date_confidence="approx",
        format="online" if geo == "intl" else "offline",
        geo=geo,
        region=region,
        language="ru",
        eligibility="ru" if geo == "ru" else "unknown",
        topics_json=dump_list(["ai"]),
        tags_json="[]",
        matched_json="[]",
        extra_sources_json='["test"]',
        first_seen_at=NOW,
        last_seen_at=NOW,
        is_active=True,
    )


def test_lipetsk_detection():
    assert (
        classify_region(
            geo="ru",
            source_region="",
            format="offline",
            text="Хакатон в Липецке",
        )
        == "lipetsk"
    )
    assert (
        classify_region(
            geo="ru",
            source_region="lipetsk",
            format="online",
            text="Онлайн-хакатон",
        )
        == "lipetsk"
    )


def test_other_region_only_when_offline():
    assert (
        classify_region(
            geo="ru",
            source_region="",
            format="offline",
            text="Хакатон в Москве",
        )
        == "other_region"
    )
    assert (
        classify_region(
            geo="ru",
            source_region="",
            format="online",
            text="Онлайн-хакатон, организатор из Москвы",
        )
        == "russia"
    )


def test_international_region():
    assert (
        classify_region(
            geo="intl",
            source_region="",
            format="online",
            text="Global hackathon",
        )
        == "international"
    )
    assert effective_region("russia", "intl") == "international"


def test_default_scope_only_russia_and_lipetsk():
    assert region_allowed("russia", "ru")
    assert region_allowed("lipetsk", "ru")
    assert not region_allowed("international", "ru")
    assert not region_allowed("other_region", "ru")
    assert region_allowed("international", "both")
    assert not region_allowed("other_region", "both")


def test_group_by_region_separates_blocks():
    groups = [
        ("ai", [make_item(1, "lipetsk"), make_item(2, "russia"), make_item(3, "international", "intl")]),
        ("web", [make_item(4, "russia")]),
    ]
    split = group_by_region(groups)
    assert [key for key, _ in split] == [
        "lipetsk|ai",
        "russia|ai",
        "russia|web",
        "international|ai",
    ]
    pages = build_pages(split, TOPICS, Settings(), html_mode=True)
    assert pages
    assert "Липецкая область" in pages[0].text
    assert "Международные" in pages[-1].text
