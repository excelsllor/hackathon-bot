from __future__ import annotations

import logging
from datetime import datetime
from urllib.parse import urlsplit

from selectolax.parser import HTMLParser

from app.pipeline.classify import HACKATHON_CUE
from app.pipeline.dates import parse_iso, parse_period
from app.pipeline.normalize import clean_text
from app.sources.base import RawItem, SourceConfig, SourceError
from app.sources.http import HttpClient

logger = logging.getLogger(__name__)


class TelegramPublicAdapter:
    kind = "telegram_public"

    async def fetch(
        self, config: SourceConfig, http: HttpClient, now: datetime
    ) -> list[RawItem]:
        html, _ = await http.get_text(config.url)
        tree = HTMLParser(html)
        messages = tree.css(".tgme_widget_message")
        if not messages:
            raise SourceError(f"{config.code}: no telegram messages found")
        items: list[RawItem] = []
        for message in messages:
            text_node = message.css_first(".tgme_widget_message_text")
            if text_node is None:
                continue
            text = clean_text(text_node.text(separator=" ", strip=True))
            if not text or not HACKATHON_CUE.search(text):
                continue
            date_node = message.css_first("time")
            link_node = message.css_first(".tgme_widget_message_date")
            url = link_node.attributes.get("href") if link_node else None
            if not url:
                continue
            published = parse_iso(date_node.attributes.get("datetime")) if date_node else None
            dates = parse_period(text, published or now, language="ru")
            if config.options.get("require_dates") and not (
                dates.starts_at or dates.ends_at or dates.reg_deadline
            ):
                continue
            if _is_past(dates, now):
                continue
            fmt = _detect_format(text)
            items.append(
                RawItem(
                    source_code=config.code,
                    external_id=_post_id(url),
                    title=_make_title(text),
                    description=text[:1500],
                    url=url,
                    starts_at=dates.starts_at,
                    ends_at=dates.ends_at,
                    reg_deadline=dates.reg_deadline,
                    date_confidence=dates.date_confidence,
                    raw_dates=dates.raw_dates,
                    format=fmt,
                    language="ru",
                    geo=config.geo,
                    eligibility="ru",
                    tags=[],
                    raw_extra={"published_at": published.isoformat() if published else None},
                )
            )
        return items


def _post_id(url: str) -> str:
    parts = urlsplit(url)
    return (parts.path or url).strip("/").replace("/", "_")[:160]


def _make_title(text: str) -> str:
    first_line = text.split("\n", 1)[0].strip()
    if not first_line:
        first_line = text
    if len(first_line) > 120:
        first_line = first_line[:119].rstrip() + "…"
    return first_line


def _is_past(dates, now: datetime) -> bool:  # type: ignore[no-untyped-def]
    markers = [dates.ends_at, dates.reg_deadline, dates.starts_at]
    values = [value for value in markers if value is not None]
    if not values:
        return False
    if dates.starts_at is not None and dates.ends_at is None and dates.reg_deadline is None:
        return dates.starts_at < now
    latest = max(values)
    return latest < now


def _detect_format(text: str) -> str:
    lowered = text.lower()
    online = any(token in lowered for token in ("онлайн", "online", "дистанц", "удаленн"))
    offline = any(token in lowered for token in ("офлайн", "offline", "очно", "москв", "петербург"))
    if online and not offline:
        return "online"
    if offline and not online:
        return "offline"
    return "unknown"
