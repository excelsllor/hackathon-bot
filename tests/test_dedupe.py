from datetime import datetime, timedelta, timezone

from app.models import Item, dump_list
from app.pipeline.dedupe import dedupe_items, is_duplicate_pair

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)


def make_item(
    item_id: int,
    title: str,
    *,
    source: str = "devpost",
    starts_at: datetime | None = None,
    days: int = 5,
) -> Item:
    start = starts_at or (NOW + timedelta(days=days))
    return Item(
        id=item_id,
        canonical_url=f"https://example.com/{item_id}",
        source_code=source,
        title=title,
        description="",
        url=f"https://example.com/{item_id}",
        starts_at=start,
        ends_at=start + timedelta(days=2),
        reg_deadline=start,
        date_confidence="approx",
        format="online",
        geo="intl",
        language="en",
        eligibility="unknown",
        topics_json=dump_list(["ai"]),
        tags_json="[]",
        matched_json="[]",
        extra_sources_json=dump_list([source]),
        first_seen_at=NOW,
        last_seen_at=NOW,
        is_active=True,
    )


def test_exact_title_is_duplicate():
    a = make_item(1, "AI Hackathon 2026", source="devpost")
    b = make_item(2, "AI Hackathon 2026", source="codenrock")
    assert is_duplicate_pair(a, b)


def test_containment_duplicate():
    a = make_item(1, "Avito ML cup 2026", source="codenrock")
    b = make_item(
        2,
        "Персональное ранжирование | Avito ML cup 2026",
        source="ods",
    )
    assert is_duplicate_pair(a, b)


def test_different_tracks_are_not_duplicates():
    a = make_item(1, "True Tech Champ 2026. Трек 1")
    b = make_item(2, "True Tech Champ 2026. Трек 2")
    assert not is_duplicate_pair(a, b)


def test_different_dates_are_not_duplicates():
    a = make_item(1, "AI Hackathon", days=5)
    b = make_item(2, "AI Hackathon", days=30)
    assert not is_duplicate_pair(a, b)


def test_unrelated_events_are_not_duplicates():
    a = make_item(1, "QNX Global Hackathon")
    b = make_item(2, "AI for Medicine: A Global Student Hackathon")
    assert not is_duplicate_pair(a, b)


def test_dedupe_keeps_one_and_merges_sources():
    a = make_item(1, "Avito ML cup 2026", source="codenrock")
    b = make_item(2, "Avito ML cup 2026", source="ods")
    result = dedupe_items([a, b])
    assert len(result) == 1
    assert set(result[0].extra_sources) == {"codenrock", "ods"}
