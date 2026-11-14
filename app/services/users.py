from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import Subscription, User


async def get_or_create_user(
    session: AsyncSession,
    *,
    platform: str,
    platform_user_id: int | str,
    username: str | None = None,
    settings: Settings | None = None,
) -> User:
    user_id = str(platform_user_id)
    user = (
        await session.execute(
            select(User).where(
                User.platform == platform, User.platform_user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if user is not None:
        if username and user.username != username:
            user.username = username[:255]
        return user
    is_admin = False
    if settings is not None:
        admin_ids = settings.tg_admins if platform == "tg" else settings.vk_admins
        is_admin = int(platform_user_id) in admin_ids if str(platform_user_id).isdigit() else False
    user = User(
        platform=platform,
        platform_user_id=user_id,
        username=username[:255] if username else None,
        is_admin=is_admin,
        digest_time=settings.digest_default_time if settings else "10:00",
        digest_days=1,
        scope_pref="ru",
        strict_eligibility=True,
    )
    session.add(user)
    await session.flush()
    return user


async def get_user(session: AsyncSession, user_id: int) -> User | None:
    return await session.get(User, user_id)


async def get_subscriptions(session: AsyncSession, user_id: int) -> list[str]:
    rows = (
        await session.execute(
            select(Subscription.topic)
            .where(Subscription.user_id == user_id)
            .order_by(Subscription.topic)
        )
    ).scalars().all()
    return list(rows)


async def toggle_subscription(session: AsyncSession, user_id: int, topic: str) -> bool:
    existing = (
        await session.execute(
            select(Subscription).where(
                Subscription.user_id == user_id, Subscription.topic == topic
            )
        )
    ).scalar_one_or_none()
    user = await session.get(User, user_id)
    if user is not None:
        user.last_digest_date = None
        user.last_digest_at = None
    if existing:
        await session.delete(existing)
        return False
    session.add(Subscription(user_id=user_id, topic=topic))
    return True


async def set_subscriptions(session: AsyncSession, user_id: int, topics: list[str]) -> None:
    await session.execute(delete(Subscription).where(Subscription.user_id == user_id))
    user = await session.get(User, user_id)
    if user is not None:
        user.last_digest_date = None
        user.last_digest_at = None
    for topic in dict.fromkeys(topics):
        session.add(Subscription(user_id=user_id, topic=topic))


def is_admin(user: User, settings: Settings) -> bool:
    if user.is_admin:
        return True
    if user.platform == "tg":
        return user.platform_user_id.isdigit() and int(user.platform_user_id) in settings.tg_admins
    if user.platform == "vk":
        return user.platform_user_id.isdigit() and int(user.platform_user_id) in settings.vk_admins
    return False
