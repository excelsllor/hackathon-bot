from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.config import Settings
from app.db import Database
from app.models import Item, Subscription, User, dump_list
from app.pipeline.classify import load_topics
from app.pipeline.dates import MSK
from app.services.digest import _is_due, run_digest_tick, send_digest_now
from app.services.search import search_items


def make_user(days: int = 127, time_value: str = "10:00", last: str | None = None) -> User:
    return User(
        id=1,
        platform="tg",
        platform_user_id="1",
        digest_enabled=True,
        digest_time=time_value,
        digest_days=days,
        last_digest_date=last,
    )


def test_due_every_day_at_time():
    now = datetime(2026, 11, 16, 10, 0, tzinfo=MSK)  # Monday
    assert _is_due(make_user(), now, now.date().isoformat())


def test_not_due_before_time():
    now = datetime(2026, 11, 16, 9, 59, tzinfo=MSK)
    assert not _is_due(make_user(), now, now.date().isoformat())


def test_not_due_if_already_sent_today():
    now = datetime(2026, 11, 16, 12, 0, tzinfo=MSK)
    assert not _is_due(make_user(last=now.date().isoformat()), now, now.date().isoformat())


def test_only_selected_weekdays():
    monday = datetime(2026, 11, 16, 10, 0, tzinfo=MSK)
    tuesday = datetime(2026, 11, 17, 10, 0, tzinfo=MSK)
    user = make_user(days=1 << 1)  # Tuesday only
    assert not _is_due(user, monday, monday.date().isoformat())
    assert _is_due(user, tuesday, tuesday.date().isoformat())


def test_no_days_never_due():
    now = datetime(2026, 11, 16, 10, 0, tzinfo=MSK)
    assert not _is_due(make_user(days=0), now, now.date().isoformat())


@pytest.mark.asyncio
async def test_auto_digest_sends_for_due_user(tmp_path):
    db = Database(tmp_path / "digest.db")
    await db.init()
    topics = load_topics(Path(__file__).resolve().parents[1] / "config" / "topics.yaml")
    settings = Settings()
    now = datetime(2026, 11, 16, 7, 5, tzinfo=timezone.utc)  # 10:05 MSK, Monday
    sent: list[str] = []

    async def sender(user_id: str, text: str) -> bool:
        sent.append(text)
        return True

    try:
        async with db.session() as session:
            user = User(
                platform="tg",
                platform_user_id="10",
                digest_enabled=True,
                digest_time="10:00",
                digest_days=127,
                scope_pref="both",
            )
            session.add(user)
            await session.flush()
            session.add(Subscription(user_id=user.id, topic="ai"))
            session.add(
                Item(
                    canonical_url="https://example.com/digest-item",
                    source_code="test",
                    title="AI Hackathon",
                    description="Описание",
                    url="https://example.com/digest-item",
                    starts_at=now + timedelta(days=2),
                    ends_at=now + timedelta(days=4),
                    reg_deadline=now + timedelta(days=1),
                    date_confidence="approx",
                    format="online",
                    geo="intl",
                    language="ru",
                    eligibility="worldwide",
                    topics_json=dump_list(["ai"]),
                    tags_json="[]",
                    matched_json="[]",
                    extra_sources_json='["test"]',
                    first_seen_at=now - timedelta(hours=1),
                    last_seen_at=now - timedelta(hours=1),
                    is_active=True,
                )
            )
        count = await run_digest_tick(
            db, settings, topics, {"tg": sender}, now=now
        )
        assert count == 1
        assert sent
        assert "AI Hackathon" in sent[0]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_manual_digest_now(tmp_path):
    db = Database(tmp_path / "digest_now.db")
    await db.init()
    topics = load_topics(Path(__file__).resolve().parents[1] / "config" / "topics.yaml")
    settings = Settings()
    now = datetime(2026, 11, 16, 7, 5, tzinfo=timezone.utc)
    sent: list[str] = []

    async def sender(user_id: str, text: str) -> bool:
        sent.append(text)
        return True

    try:
        async with db.session() as session:
            user = User(
                platform="tg",
                platform_user_id="11",
                digest_enabled=False,
                digest_time="10:00",
                digest_days=127,
                scope_pref="both",
            )
            session.add(user)
            await session.flush()
            session.add(Subscription(user_id=user.id, topic="ai"))
            session.add(
                Item(
                    canonical_url="https://example.com/manual-item",
                    source_code="test",
                    title="Manual AI Hackathon",
                    description="",
                    url="https://example.com/manual-item",
                    starts_at=now + timedelta(days=2),
                    ends_at=now + timedelta(days=4),
                    reg_deadline=now + timedelta(days=1),
                    date_confidence="approx",
                    format="online",
                    geo="intl",
                    language="ru",
                    eligibility="worldwide",
                    topics_json=dump_list(["ai"]),
                    tags_json="[]",
                    matched_json="[]",
                    extra_sources_json='["test"]',
                    first_seen_at=now - timedelta(hours=1),
                    last_seen_at=now - timedelta(hours=1),
                    is_active=True,
                )
            )
            await session.flush()
            user_id = user.id
        async with db.session() as session:
            user = await session.get(User, user_id)
        assert user is not None
        status = await send_digest_now(db, settings, topics, sender, user, now=now)
        assert status == "sent"
        assert sent and "Manual AI Hackathon" in sent[0]
        status = await send_digest_now(db, settings, topics, sender, user, now=now)
        assert status == "sent"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_digest_matches_search_and_dedupes(tmp_path):
    db = Database(tmp_path / "digest_parity.db")
    await db.init()
    topics = load_topics(Path(__file__).resolve().parents[1] / "config" / "topics.yaml")
    settings = Settings()
    now = datetime(2026, 11, 16, 7, 5, tzinfo=timezone.utc)
    sent: list[str] = []

    async def sender(user_id: str, text: str) -> bool:
        sent.append(text)
        return True

    def make_item(item_id: int, title: str, first_seen, source: str) -> Item:
        return Item(
            id=item_id,
            canonical_url=f"https://example.com/{item_id}",
            source_code=source,
            title=title,
            description="",
            url=f"https://example.com/{item_id}",
            starts_at=now + timedelta(days=2),
            ends_at=now + timedelta(days=4),
            reg_deadline=now + timedelta(days=1),
            date_confidence="exact",
            format="online",
            geo="intl",
            language="ru",
            eligibility="worldwide",
            topics_json=dump_list(["ai"]),
            tags_json="[]",
            matched_json="[]",
            extra_sources_json=f'["{source}"]',
            first_seen_at=first_seen,
            last_seen_at=first_seen,
            is_active=True,
        )

    try:
        async with db.session() as session:
            user = User(
                platform="tg",
                platform_user_id="13",
                digest_enabled=True,
                digest_time="10:00",
                digest_days=1,
                scope_pref="both",
            )
            session.add(user)
            await session.flush()
            session.add(Subscription(user_id=user.id, topic="ai"))
            session.add(
                make_item(
                    1,
                    "Old AI Hackathon",
                    now - timedelta(days=40),
                    "old-source",
                )
            )
            session.add(
                make_item(
                    2,
                    "Duplicate AI Hackathon",
                    now - timedelta(hours=2),
                    "source-a",
                )
            )
            session.add(
                make_item(
                    3,
                    "Duplicate AI Hackathon",
                    now - timedelta(hours=2),
                    "source-b",
                )
            )
            await session.flush()
            user_id = user.id

        async with db.session() as session:
            user = await session.get(User, user_id)
            search_groups = await search_items(session, user, ["ai"], topics, now=now)
            search_count = sum(len(items) for _, items in search_groups)
            user = await session.get(User, user_id)
        status = await send_digest_now(db, settings, topics, sender, user, now=now)
        assert status == "sent"
        text = "\n".join(sent)
        assert search_count == 2
        assert "Old AI Hackathon" in text
        assert text.count("Duplicate AI Hackathon") == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_new_user_defaults_are_weekly_russia(tmp_path):
    from app.services.users import get_or_create_user

    db = Database(tmp_path / "defaults.db")
    await db.init()
    try:
        async with db.session() as session:
            user = await get_or_create_user(
                session,
                platform="tg",
                platform_user_id="12",
                settings=Settings(),
            )
        assert user.digest_days == 1
        assert user.scope_pref == "ru"
        assert user.digest_time == "10:00"
        assert user.strict_eligibility is True
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_schema_migration_adds_digest_days(tmp_path):
    import sqlite3

    from app.db import Database

    path = tmp_path / "old.db"
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            platform VARCHAR(8),
            platform_user_id VARCHAR(64),
            username VARCHAR(255),
            is_admin BOOLEAN,
            digest_enabled BOOLEAN,
            digest_time VARCHAR(5),
            format_pref VARCHAR(8),
            scope_pref VARCHAR(8),
            strict_eligibility BOOLEAN,
            last_digest_date VARCHAR(10),
            last_digest_at DATETIME,
            created_at DATETIME
        )
        """
    )
    connection.execute(
        "INSERT INTO users (id, platform, platform_user_id) VALUES (1, 'tg', '1')"
    )
    connection.commit()
    connection.close()

    db = Database(path)
    await db.init()
    try:
        connection = sqlite3.connect(path)
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(users)").fetchall()
        }
        value = connection.execute(
            "SELECT digest_days FROM users WHERE id = 1"
        ).fetchone()[0]
        strict = connection.execute(
            "SELECT strict_eligibility FROM users WHERE id = 1"
        ).fetchone()[0]
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        connection.close()
        assert "digest_days" in columns
        assert value == 1
        assert strict == 1
        assert version == 2
    finally:
        await db.close()
