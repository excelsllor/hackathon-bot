from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from selectolax.parser import HTMLParser

from app.pipeline.dates import parse_iso
from app.pipeline.normalize import clean_text
from app.sources.base import RawItem, SourceConfig, SourceError
from app.sources.http import HttpClient

logger = logging.getLogger(__name__)


class DevfolioAdapter:
    kind = "devfolio_html"

    async def fetch(
        self, config: SourceConfig, http: HttpClient, now: datetime
    ) -> list[RawItem]:
        html, _ = await http.get_text(config.url)
        data = _extract_next_data(html)
        page_props = (
            data.get("props", {}).get("pageProps", {}) if isinstance(data, dict) else {}
        )
        queries = (
            page_props.get("dehydratedState", {}).get("queries", [])
            if isinstance(page_props, dict)
            else []
        )
        if not queries:
            raise SourceError(f"{config.code}: no dehydrated state")
        payload = queries[0].get("state", {}).get("data") or {}
        if not isinstance(payload, dict):
            raise SourceError(f"{config.code}: unexpected payload")

        seen: set[str] = set()
        items: list[RawItem] = []
        for group in ("open_hackathons", "upcoming_hackathons"):
            for entry in payload.get(group) or []:
                if not isinstance(entry, dict):
                    continue
                slug = str(entry.get("slug") or "")
                if not slug or slug in seen:
                    continue
                seen.add(slug)
                item = _parse_entry(entry, config, now)
                if item:
                    items.append(item)
        return items


def _extract_next_data(html: str) -> dict[str, Any]:
    tree = HTMLParser(html)
    node = tree.css_first("script#__NEXT_DATA__")
    if node is None:
        raise SourceError("Devfolio: __NEXT_DATA__ not found")
    try:
        data = json.loads(node.text())
    except ValueError as exc:
        raise SourceError("Devfolio: invalid __NEXT_DATA__") from exc
    if not isinstance(data, dict):
        raise SourceError("Devfolio: unexpected __NEXT_DATA__")
    return data


def _parse_entry(entry: dict, config: SourceConfig, now: datetime) -> RawItem | None:
    name = clean_text(str(entry.get("name") or ""))
    slug = str(entry.get("slug") or "")
    if not name or not slug:
        return None
    starts_at = parse_iso(entry.get("starts_at"))
    ends_at = parse_iso(entry.get("ends_at"))
    settings = entry.get("settings") or {}
    reg_deadline = parse_iso(settings.get("reg_ends_at")) or ends_at
    if ends_at is not None and ends_at < now:
        return None

    is_online = bool(entry.get("is_online"))
    fmt = "online" if is_online else "offline"
    if config.online_only and not is_online:
        return None

    themes = []
    for theme in entry.get("themes") or []:
        if isinstance(theme, dict):
            value = theme.get("theme") or {}
            if isinstance(value, dict) and value.get("name"):
                themes.append(clean_text(str(value["name"])))

    participants = entry.get("participants_count")
    description_parts = ["Devfolio."]
    if themes:
        description_parts.append("Темы: " + ", ".join(themes) + ".")
    description_parts.append("Формат: онлайн." if is_online else "Формат: офлайн.")
    if participants:
        description_parts.append(f"Участников: {participants}.")
    description = clean_text(" ".join(description_parts), 1000)

    return RawItem(
        source_code=config.code,
        external_id=str(entry.get("uuid") or slug),
        title=name[:600],
        description=description,
        url=f"https://devfolio.co/hackathons/{slug}",
        starts_at=starts_at,
        ends_at=ends_at,
        reg_deadline=reg_deadline,
        date_confidence="exact" if starts_at else "unknown",
        raw_dates=f"{entry.get('starts_at') or ''} - {entry.get('ends_at') or ''}".strip(" -"),
        format=fmt,
        city=None,
        country=None,
        language="en",
        geo=config.geo,
        eligibility="unknown",
        tags=themes,
        raw_extra={"participants_count": participants},
    )
