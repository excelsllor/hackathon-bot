from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.config import Settings
from app.db import Database
from app.models import Item, User, dump_list
from app.pipeline.classify import load_topics
from app.services.search import hide_item, search_items
from app.services.users import get_or_create_user

TOPICS = load_topics(Path(__file__).resolve().parents[1] / "config" / "topics.yaml")
SETTINGS = Settings()


def item(
    item_id: int,
    title: str,
    *,
    topics: list[str],
    format: str = "online",
    geo: str = "intl",
    eligibility: str = "worldwide",
    source: str = "test",
) -> Item:
    now = datetime.now(timezone.utc)
    return Item(
        id=item_id,
        canonical_url=f"https://example.com/{item_id}",
        source_code=source,
        title=title,
        description="description",
        url=f"https://example.com/{item_id}",
        starts_at=now + timedelta(days=3),
        ends_at=now + timedelta(days=10),
        reg_deadline=now + timedelta(days=5),
        date_confidence="approx",
        format=format,
        geo=geo,
        language="ru",
        eligibility=eligibility,
        topics_json=dump_list(topics),
        tags_json="[]",
        matched_json="[]",
        extra_sources_json='["test"]',
        first_seen_at=now,
        last_seen_at=now,
        is_active=True,
    )


@pytest.mark.asyncio
async def test_search_filters_and_groups(tmp_path):
    db = Database(tmp_path / "test.db")
    await db.init()
    try:
        async with db.session() as session:
            user = await get_or_create_user(
                session,
                platform="tg",
                platform_user_id=42,
                settings=SETTINGS,
            )
            user.scope_pref = "both"
            session.add_all(
                [
                    item(1, "AI online worldwide", topics=["ai"]),
                    item(2, "AI online restricted", topics=["ai"], eligibility="restricted"),
                    item(3, "AI offline", topics=["ai"], format="offline"),
                    item(4, "Web online", topics=["web"]),
                    item(
                        5,
                        "RU offline",
                        topics=["ai"],
                        format="offline",
                        geo="ru",
                        eligibility="ru",
                    ),
                    item(6, "AI online unknown", topics=["ai"], eligibility="unknown"),
                ]
            )

        async with db.session() as session:
            user = await session.get(User, user.id)
            groups = await search_items(session, user, ["ai"], TOPICS)
        assert len(groups) == 1
        assert groups[0][0] == "ai"
        assert {item.title for item in groups[0][1]} == {
            "AI online worldwide",
            "RU offline",
        }

        async with db.session() as session:
            user = await session.get(User, user.id)
            user.format_pref = "online"
            groups = await search_items(session, user, ["ai"], TOPICS)
        assert {item.title for item in groups[0][1]} == {"AI online worldwide"}

        async with db.session() as session:
            user = await session.get(User, user.id)
            user.format_pref = "any"
            groups = await search_items(session, user, ["ai"], TOPICS)
        assert {item.title for item in groups[0][1]} == {
            "AI online worldwide",
            "RU offline",
        }

        async with db.session() as session:
            user = await session.get(User, user.id)
            user.strict_eligibility = False
            await hide_item(session, user.id, 1)
        async with db.session() as session:
            user = await session.get(User, user.id)
            groups = await search_items(session, user, ["ai"], TOPICS)
        assert {item.title for item in groups[0][1]} == {"RU offline"}
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_search_deduplicates_same_event_from_two_sources(tmp_path):
    db = Database(tmp_path / "dedupe.db")
    await db.init()
    try:
        async with db.session() as session:
            user = await get_or_create_user(
                session, platform="tg", platform_user_id=99, settings=SETTINGS
            )
            user.scope_pref = "both"
            session.add_all(
                [
                    item(1, "Avito ML cup 2026", topics=["ai"], source="codenrock"),
                    item(2, "Avito ML cup 2026", topics=["ai"], source="ods"),
                ]
            )
        async with db.session() as session:
            user = await session.get(User, user.id)
            groups = await search_items(session, user, ["ai"], TOPICS)
        assert len(groups) == 1
        assert len(groups[0][1]) == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_scope_preference(tmp_path):
    db = Database(tmp_path / "scope.db")
    await db.init()
    try:
        async with db.session() as session:
            user = await get_or_create_user(
                session, platform="tg", platform_user_id=7, settings=SETTINGS
            )
            session.add_all(
                [
                    item(10, "RU event", topics=["ai"], geo="ru", eligibility="ru"),
                    item(11, "INTL event", topics=["ai"], geo="intl"),
                ]
            )
        async with db.session() as session:
            user = await session.get(User, user.id)
            user.scope_pref = "ru"
            groups = await search_items(session, user, ["ai"], TOPICS)
        assert [item.title for item in groups[0][1]] == ["RU event"]
    finally:
        await db.close()
