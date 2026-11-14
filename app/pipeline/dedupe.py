from __future__ import annotations

import json
import re
from collections.abc import Iterable
from datetime import timedelta

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import naive_utc
from app.models import Item, ensure_utc
from app.pipeline.normalize import title_key

GENERIC_TOKENS = {
    "hackathon",
    "хакатон",
    "online",
    "онлайн",
    "offline",
    "офлайн",
    "global",
    "international",
    "международный",
    "student",
    "студенческий",
    "project",
    "проект",
    "challenge",
    "челлендж",
    "cup",
    "кубок",
    "contest",
    "конкурс",
    "competition",
    "соревнование",
    "чемпионат",
    "2024",
    "2025",
    "2026",
    "2027",
}

_TRACK_RE = re.compile(r"(?:трек|track)\s*[№#]?\s*(\d+)", re.IGNORECASE)


def is_duplicate_pair(a: Item, b: Item, *, same_source: bool | None = None) -> bool:
    if a.canonical_url == b.canonical_url:
        return True
    if same_source is None:
        same_source = a.source_code == b.source_code
    if not _dates_close(a.starts_at, b.starts_at):
        return False
    if _tracks_conflict(a.title, b.title):
        return False

    key_a = title_key(a.title)
    key_b = title_key(b.title)
    if not key_a or not key_b:
        return False
    if key_a == key_b:
        return True

    sig_a = _significant_key(key_a)
    sig_b = _significant_key(key_b)
    if sig_a and sig_b and min(len(sig_a), len(sig_b)) >= 8:
        if sig_a in sig_b or sig_b in sig_a:
            return True
        tokens_a = set(sig_a.split())
        tokens_b = set(sig_b.split())
        union = tokens_a | tokens_b
        if (
            union
            and len(tokens_a & tokens_b) / len(union) >= 0.8
            and fuzz.token_set_ratio(key_a, key_b) >= 84
        ):
            return True

    ratio = fuzz.token_set_ratio(key_a, key_b)
    threshold = 96 if same_source else 90
    if a.starts_at is None or b.starts_at is None:
        threshold = 96
    return ratio >= threshold


def dedupe_items(items: Iterable[Item]) -> list[Item]:
    unique: list[Item] = []
    for item in items:
        duplicate = next(
            (existing for existing in unique if is_duplicate_pair(item, existing)),
            None,
        )
        if duplicate is None:
            unique.append(item)
        else:
            _merge_display_fields(duplicate, item)
    return unique


async def find_duplicate(
    session: AsyncSession,
    *,
    title: str,
    starts_at,
    source_code: str,
    exclude_item_id: int | None = None,
) -> Item | None:
    target = title_key(title)
    if len(target) < 8:
        return None

    query = select(Item).where(Item.is_active.is_(True))
    if exclude_item_id:
        query = query.where(Item.id != exclude_item_id)
    if starts_at is not None:
        start = naive_utc(starts_at)
        if start is not None:
            query = query.where(
                Item.starts_at.is_not(None),
                Item.starts_at >= start - timedelta(days=3),
                Item.starts_at <= start + timedelta(days=3),
            )
    query = query.order_by(Item.first_seen_at.desc()).limit(400)
    rows = (await session.execute(query)).scalars().all()

    candidate = Item(
        canonical_url="",
        source_code=source_code,
        title=title,
        url="",
        starts_at=starts_at,
    )
    best: tuple[float, Item] | None = None
    for existing in rows:
        if not is_duplicate_pair(candidate, existing):
            continue
        ratio = fuzz.token_set_ratio(target, title_key(existing.title))
        if best is None or ratio > best[0]:
            best = (ratio, existing)
    return best[1] if best else None


def _dates_close(a, b, days: int = 3) -> bool:
    left = ensure_utc(a)
    right = ensure_utc(b)
    if left is None or right is None:
        return True
    return abs((left - right).total_seconds()) <= days * 86400


def _tracks_conflict(a: str, b: str) -> bool:
    left = {match.group(1) for match in _TRACK_RE.finditer(a or "")}
    right = {match.group(1) for match in _TRACK_RE.finditer(b or "")}
    return bool(left and right and left != right)


def _significant_key(key: str) -> str:
    tokens = [
        token
        for token in key.split()
        if token not in GENERIC_TOKENS and not token.isdigit() and len(token) > 1
    ]
    return " ".join(tokens)


def _merge_display_fields(primary: Item, duplicate: Item) -> None:
    if not primary.description and duplicate.description:
        primary.description = duplicate.description
    if primary.reg_deadline is None and duplicate.reg_deadline is not None:
        primary.reg_deadline = duplicate.reg_deadline
    if not primary.raw_dates and duplicate.raw_dates:
        primary.raw_dates = duplicate.raw_dates
    sources = set(primary.extra_sources) | {primary.source_code, duplicate.source_code}
    primary.extra_sources_json = json.dumps(sorted(sources), ensure_ascii=False)
