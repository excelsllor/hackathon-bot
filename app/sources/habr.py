from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import feedparser

from app.pipeline.classify import HACKATHON_CUE
from app.pipeline.dates import parse_period
from app.pipeline.normalize import clean_text, strip_html
from app.sources.base import RawItem, SourceConfig
from app.sources.http import HttpClient

RETROSPECTIVE = re.compile(
    r"(?i)\b(итоги|перевод|обзор|гайд|туториал|разбор|кейс|ретроспектив\w*|"
    r"подборка|как мы|как прошел|как прошёл|как побеждать|как выиграть|"
    r"как выбрать|подошел к концу|подошёл к концу|завершил\w*)\b"
)
ACTIONABLE = re.compile(
    r"(?i)(регистрац\w*|заявк\w*|прием заявок|подача заявок|отбор\w*|"
    r"участ\w*|register|apply|deadline)"
)

logger = logging.getLogger(__name__)


class HabrRssAdapter:
    kind = "habr_rss"

    async def fetch(
        self, config: SourceConfig, http: HttpClient, now: datetime
    ) -> list[RawItem]:
        text, _ = await http.get_text(config.url)
        feed = feedparser.parse(text)
        items: list[RawItem] = []
        exclude_regex = None
        raw_exclude = config.options.get("exclude_title_regex")
        if isinstance(raw_exclude, str) and raw_exclude.strip():
            exclude_regex = re.compile(raw_exclude)
        require_dates = bool(config.options.get("require_dates"))
        for entry in feed.entries:
            title = clean_text(str(entry.get("title") or ""))
            link = str(entry.get("link") or "").strip()
            if not title or not link:
                continue
            if exclude_regex and exclude_regex.search(title):
                continue
            if RETROSPECTIVE.search(title):
                continue
            description = strip_html(str(entry.get("summary") or entry.get("description") or ""))
            tags = [str(t.get("term") or "") for t in entry.get("tags", []) if t.get("term")]
            combined = f"{title} {description}"
            if not HACKATHON_CUE.search(combined):
                continue

            published = _published(entry)
            dates = parse_period(combined, published or now, language="ru")
            has_dates = bool(dates.starts_at or dates.ends_at or dates.reg_deadline)
            if require_dates and not has_dates:
                continue
            if not has_dates and not ACTIONABLE.search(combined):
                continue
            if _is_past(dates, now):
                continue
            fmt = _detect_format(combined)
            items.append(
                RawItem(
                    source_code=config.code,
                    external_id=str(entry.get("id") or link)[:160],
                    title=title[:600],
                    description=clean_text(description, 1200),
                    url=link,
                    starts_at=dates.starts_at,
                    ends_at=dates.ends_at,
                    reg_deadline=dates.reg_deadline,
                    date_confidence=dates.date_confidence,
                    raw_dates=dates.raw_dates,
                    format=fmt,
                    language="ru",
                    geo=config.geo,
                    eligibility="ru",
                    tags=tags,
                )
            )
        return items


def _published(entry) -> datetime | None:  # type: ignore[no-untyped-def]
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        return datetime(*parsed[:6], tzinfo=timezone.utc)
    return None


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
