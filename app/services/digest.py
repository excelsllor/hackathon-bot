from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from sqlalchemy import select

from app.bots.render import build_digest_chunks, group_by_region
from app.config import Settings
from app.db import Database
from app.models import Delivery, Subscription, User
from app.pipeline.classify import TopicsConfig
from app.pipeline.dates import MSK
from app.services.search import search_items

logger = logging.getLogger(__name__)

Sender = Callable[[str, str], Awaitable[bool]]


async def run_digest_tick(
    db: Database,
    settings: Settings,
    topics: TopicsConfig,
    senders: dict[str, Sender],
    now: datetime | None = None,
) -> int:
    now = now or datetime.now(timezone.utc)
    now_msk = now.astimezone(MSK)
    today = now_msk.date().isoformat()
    sent_users = 0

    async with db.session() as session:
        users = (
            await session.execute(select(User).where(User.digest_enabled.is_(True)))
        ).scalars().all()

    for user in users:
        if not _is_due(user, now_msk, today):
            continue
        sender = senders.get(user.platform)
        if sender is None:
            continue
        logger.info(
            "digest: due user=%s/%s days=%s time=%s",
            user.platform,
            user.platform_user_id,
            user.digest_days,
            user.digest_time,
        )
        try:
            status = await _send_user_digest(
                session_factory=db,
                user=user,
                settings=settings,
                topics=topics,
                sender=sender,
                now=now,
                today=today,
            )
            if status == "sent":
                sent_users += 1
        except Exception:
            logger.exception(
                "digest failed for user %s/%s", user.platform, user.platform_user_id
            )
    return sent_users


async def send_digest_now(
    db: Database,
    settings: Settings,
    topics: TopicsConfig,
    sender: Sender,
    user: User,
    now: datetime | None = None,
) -> str:
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(MSK).date().isoformat()
    return await _send_user_digest(
        session_factory=db,
        user=user,
        settings=settings,
        topics=topics,
        sender=sender,
        now=now,
        today=today,
    )


def _is_due(user: User, now_msk: datetime, today: str) -> bool:
    if user.last_digest_date == today:
        return False
    days = user.digest_days if user.digest_days is not None else 127
    if not days & (1 << now_msk.weekday()):
        return False
    return now_msk.strftime("%H:%M") >= (user.digest_time or "10:00")


async def _send_user_digest(
    *,
    session_factory: Database,
    user: User,
    settings: Settings,
    topics: TopicsConfig,
    sender: Sender,
    now: datetime,
    today: str,
) -> str:
    async with session_factory.session() as session:
        user_db = await session.get(User, user.id)
        if user_db is None:
            return "missing_user"
        sub_rows = (
            await session.execute(
                select(Subscription.topic).where(Subscription.user_id == user_db.id)
            )
        ).scalars().all()
        subscriptions = [t for t in topics.order if t in set(sub_rows)]
        if not subscriptions:
            logger.info(
                "digest: no subscriptions for user=%s/%s",
                user_db.platform,
                user_db.platform_user_id,
            )
            return "no_topics"

        groups = await search_items(
            session,
            user_db,
            subscriptions,
            topics,
            limit=settings.max_items_per_search,
            now=now,
        )
        if not groups:
            logger.info(
                "digest: no current items for user=%s/%s",
                user_db.platform,
                user_db.platform_user_id,
            )
            user_db.last_digest_date = today
            user_db.last_digest_at = now
            return "empty"

        groups = group_by_region(groups)
        chunks = build_digest_chunks(groups, topics, settings, html_mode=False)
        intro = "🗓 Дайджест по твоим темам\n\n"
        if chunks:
            chunks[0] = intro + chunks[0]

        sent_any = False
        for chunk in chunks:
            ok = await sender(user_db.platform_user_id, chunk)
            if not ok:
                logger.warning(
                    "digest: send failed for user=%s/%s",
                    user_db.platform,
                    user_db.platform_user_id,
                )
                return "send_failed"
            sent_any = True

        if not sent_any:
            return "send_failed"

        item_ids = {item.id for _, topic_items in groups for item in topic_items}
        existing_deliveries = set(
            (
                await session.execute(
                    select(Delivery.item_id).where(
                        Delivery.user_id == user_db.id,
                        Delivery.kind == "digest",
                        Delivery.item_id.in_(item_ids),
                    )
                )
            )
            .scalars()
            .all()
        )
        for _topic, topic_items in groups:
            for item in topic_items:
                if item.id in existing_deliveries:
                    continue
                session.add(
                    Delivery(user_id=user_db.id, item_id=item.id, kind="digest")
                )
        user_db.last_digest_date = today
        user_db.last_digest_at = now
        logger.info(
            "digest: sent %s items in %s chunks to user=%s/%s",
            sum(len(items) for _, items in groups),
            len(chunks),
            user_db.platform,
            user_db.platform_user_id,
        )
        return "sent"
