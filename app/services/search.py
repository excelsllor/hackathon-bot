from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import FavoriteItem, HiddenItem, Item, User, ensure_utc
from app.pipeline.classify import TopicsConfig
from app.pipeline.dedupe import dedupe_items
from app.pipeline.eligibility import is_visible
from app.pipeline.regions import effective_region, region_allowed


async def search_items(
    session: AsyncSession,
    user: User,
    topic_ids: list[str],
    config: TopicsConfig,
    *,
    limit: int = 120,
    now: datetime | None = None,
) -> list[tuple[str, list[Item]]]:
    now = now or datetime.now(timezone.utc)
    if not topic_ids:
        return []

    hidden = select(HiddenItem.item_id).where(HiddenItem.user_id == user.id)
    query = (
        select(Item)
        .where(Item.is_active.is_(True), Item.id.not_in(hidden))
        .order_by(Item.reg_deadline.is_(None), Item.reg_deadline, Item.starts_at)
        .limit(3000)
    )
    items = (await session.execute(query)).scalars().all()

    selected = [t for t in config.order if t in set(topic_ids)]
    if not selected:
        return []

    groups: dict[str, list[Item]] = {topic: [] for topic in selected}
    eligible: list[Item] = []
    for item in items:
        if not _is_current(item, now):
            continue
        item_region = effective_region(item.region, item.geo)
        if not region_allowed(item_region, user.scope_pref):
            continue
        if item.geo == "intl" and item.format != "online":
            continue
        if user.format_pref != "any" and item.format != user.format_pref:
            continue
        if not is_visible(item.eligibility, geo=item.geo, strict=True):
            continue
        if not [t for t in item.topics if t in groups]:
            continue
        eligible.append(item)

    for item in dedupe_items(eligible)[:limit]:
        item_topics = [t for t in item.topics if t in groups]
        topic = selected[0]
        for candidate in selected:
            if candidate in item_topics:
                topic = candidate
                break
        groups[topic].append(item)

    result: list[tuple[str, list[Item]]] = []
    for topic in selected:
        topic_items = groups[topic]
        if not topic_items:
            continue
        topic_items.sort(key=_sort_key)
        result.append((topic, topic_items))
    return result


def _is_current(item: Item, now: datetime) -> bool:
    deadline = ensure_utc(item.reg_deadline)
    ends = ensure_utc(item.ends_at)
    starts = ensure_utc(item.starts_at)
    if ends is not None and ends < now - timedelta(days=1):
        return False
    if deadline is not None and deadline < now - timedelta(days=1):
        return False
    return not (
        ends is None and starts is not None and starts < now - timedelta(days=45)
    )


def _sort_key(item: Item):
    value = ensure_utc(item.reg_deadline) or ensure_utc(item.starts_at) or ensure_utc(item.ends_at)
    return (value is None, value or datetime.max.replace(tzinfo=timezone.utc))


async def hide_item(session: AsyncSession, user_id: int, item_id: int) -> None:
    exists = (
        await session.execute(
            select(HiddenItem).where(
                HiddenItem.user_id == user_id, HiddenItem.item_id == item_id
            )
        )
    ).scalar_one_or_none()
    if not exists:
        session.add(HiddenItem(user_id=user_id, item_id=item_id))


async def favorite_item(session: AsyncSession, user_id: int, item_id: int) -> bool:
    exists = (
        await session.execute(
            select(FavoriteItem).where(
                FavoriteItem.user_id == user_id, FavoriteItem.item_id == item_id
            )
        )
    ).scalar_one_or_none()
    if exists:
        return False
    session.add(FavoriteItem(user_id=user_id, item_id=item_id))
    return True


async def get_item(session: AsyncSession, item_id: int) -> Item | None:
    return await session.get(Item, item_id)


async def stats(session: AsyncSession) -> dict[str, int]:
    items = (await session.execute(select(func.count(Item.id)))).scalar_one()
    active = (
        await session.execute(select(func.count(Item.id)).where(Item.is_active.is_(True)))
    ).scalar_one()
    users = (await session.execute(select(func.count(User.id)))).scalar_one()
    return {"items": int(items), "active_items": int(active), "users": int(users)}
