from __future__ import annotations

import html
import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.config import Settings
from app.models import Item, ensure_utc
from app.pipeline.classify import TopicsConfig
from app.pipeline.eligibility import eligibility_badge
from app.pipeline.normalize import clean_text, strip_html, truncate
from app.pipeline.regions import REGION_LABELS, REGION_ORDER, effective_region

MSK = ZoneInfo("Europe/Moscow")


@dataclass(slots=True)
class Page:
    topic_id: str
    items: list[Item]
    text: str


def format_dt(value: datetime | None) -> str:
    if value is None:
        return ""
    local = ensure_utc(value)
    if local is None:
        return ""
    return local.astimezone(MSK).strftime("%d.%m.%Y")


def days_left(value: datetime | None, now: datetime | None = None) -> str:
    if value is None:
        return ""
    now = now or datetime.now(timezone.utc)
    target = ensure_utc(value)
    if target is None:
        return ""
    delta = (target - now).total_seconds() / 86400
    if delta < 0:
        return "закрыта"
    if delta < 1:
        return "сегодня"
    return f"{math.ceil(delta)} дн."


def _format_badge(item: Item) -> str:
    return eligibility_badge(item.eligibility)


def _format_mode(item: Item) -> str:
    if item.format == "online":
        return "🌐 Онлайн"
    if item.format == "offline":
        location = f" ({item.city})" if item.city else ""
        return f"📍 Офлайн{location}"
    if item.format == "hybrid":
        return "🔀 Гибрид"
    return "❔ Формат не указан"


def _format_topics(item: Item, config: TopicsConfig) -> str:
    topics = [config.display(t) for t in item.topics[:4]]
    return " · ".join(topics)


def item_card(
    item: Item,
    config: TopicsConfig,
    settings: Settings,
    *,
    html_mode: bool,
    number: int | None = None,
) -> str:
    escape = html.escape if html_mode else (lambda value, quote=False: value)
    title = escape(clean_text(item.title), quote=False)
    description = truncate(strip_html(item.description), settings.description_max_chars)
    description = escape(description, quote=False)

    lines: list[str] = []
    prefix = f"{number}. " if number else ""
    if html_mode:
        lines.append(f"<b>{prefix}{title}</b>")
    else:
        lines.append(f"{prefix}{title}")

    deadline = item.reg_deadline or item.ends_at
    if deadline:
        left = days_left(deadline)
        suffix = f" · осталось {left}" if left and left != "закрыта" else ""
        label = "Регистрация до" if item.reg_deadline else "Завершение"
        lines.append(f"📅 {label}: {format_dt(deadline)}{suffix}")
    elif item.starts_at:
        lines.append(f"🗓 Старт: {format_dt(item.starts_at)}")
    else:
        lines.append("📅 Дата не указана")

    lines.append(f"{_format_mode(item)} · {_format_badge(item)}")
    topics = _format_topics(item, config)
    if topics:
        lines.append(f"🏷 {topics}")
    if description:
        lines.append(description)
    url = escape(item.url, quote=True) if html_mode else item.url
    if html_mode:
        lines.append(f'🔗 <a href="{url}">Открыть</a>')
    else:
        lines.append(f"🔗 {url}")
    return "\n".join(lines)


def group_by_region(
    groups: Iterable[tuple[str, list[Item]]]
) -> list[tuple[str, list[Item]]]:
    source_groups = list(groups)
    result: list[tuple[str, list[Item]]] = []
    for region in REGION_ORDER:
        for topic_id, items in source_groups:
            bucket = [
                item
                for item in items
                if effective_region(item.region, item.geo) == region
            ]
            if bucket:
                result.append((f"{region}|{topic_id}", bucket))
    return result


def _group_header(topic_key: str, config: TopicsConfig, html_mode: bool) -> str:
    if "|" in topic_key:
        region, topic_id = topic_key.split("|", 1)
        label = f"{REGION_LABELS.get(region, region)} ? {config.emoji(topic_id)} {config.title(topic_id)}"
    else:
        label = f"{config.emoji(topic_key)} {config.title(topic_key)}"
    return html.escape(label, quote=False) if html_mode else label


def build_pages(
    groups: Iterable[tuple[str, list[Item]]],
    config: TopicsConfig,
    settings: Settings,
    *,
    html_mode: bool = True,
    page_size: int | None = None,
) -> list[Page]:
    limit = page_size or settings.page_size
    pages: list[Page] = []
    for topic_id, items in groups:
        if not items:
            continue
        header = _group_header(topic_id, config, html_mode)
        if html_mode:
            header = f"<b>{header}</b>"
        chunk: list[Item] = []
        chunk_texts: list[str] = []
        for item in items:
            card = item_card(item, config, settings, html_mode=html_mode, number=len(chunk) + 1)
            candidate = "\n\n".join([header] + chunk_texts + [card])
            if chunk and (
                len(chunk) >= limit or len(candidate) > settings.message_max_chars
            ):
                pages.append(
                    Page(
                        topic_id=topic_id,
                        items=chunk,
                        text="\n\n".join([header] + chunk_texts),
                    )
                )
                chunk = []
                chunk_texts = []
                card = item_card(item, config, settings, html_mode=html_mode, number=1)
            chunk.append(item)
            chunk_texts.append(card)
        if chunk:
            pages.append(
                Page(
                    topic_id=topic_id,
                    items=chunk,
                    text="\n\n".join([header] + chunk_texts),
                )
            )
    return pages


def build_digest_chunks(
    groups: Iterable[tuple[str, list[Item]]],
    config: TopicsConfig,
    settings: Settings,
    *,
    html_mode: bool = False,
) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    for topic_id, items in groups:
        if not items:
            continue
        header = _group_header(topic_id, config, html_mode)
        for item in items:
            card = item_card(item, config, settings, html_mode=html_mode)
            block = card
            if not current:
                block = header + "\n\n" + card
            candidate = "\n\n".join(current + [block])
            if current and len(candidate) > settings.message_max_chars:
                chunks.append("\n\n".join(current))
                current = [header + "\n\n" + card]
            else:
                current.append(block)
    if current:
        chunks.append("\n\n".join(current))
    return chunks
