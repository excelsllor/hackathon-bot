from __future__ import annotations

import json
import logging
from datetime import datetime

from selectolax.parser import HTMLParser

from app.pipeline.dates import parse_iso
from app.pipeline.normalize import clean_text
from app.sources.base import RawItem, SourceConfig, SourceError
from app.sources.http import HttpClient

logger = logging.getLogger(__name__)


class OdsAdapter:
    kind = "ods_html"

    async def fetch(
        self, config: SourceConfig, http: HttpClient, now: datetime
    ) -> list[RawItem]:
        html, _ = await http.get_text(config.url)
        tree = HTMLParser(html)
        node = tree.css_first("script#__NEXT_DATA__")
        if node is None:
            raise SourceError(f"{config.code}: __NEXT_DATA__ not found")
        try:
            data = json.loads(node.text())
        except ValueError as exc:
            raise SourceError(f"{config.code}: invalid __NEXT_DATA__") from exc
        page_props = data.get("props", {}).get("pageProps", {}) if isinstance(data, dict) else {}
        active = page_props.get("activePageData", {}) if isinstance(page_props, dict) else {}
        competitions = active.get("competitions") or []
        if not isinstance(competitions, list):
            raise SourceError(f"{config.code}: competitions is not a list")

        items: list[RawItem] = []
        for entry in competitions:
            if not isinstance(entry, dict):
                continue
            item = _parse_entry(entry, config, now)
            if item:
                items.append(item)
        return items


def _parse_entry(entry: dict, config: SourceConfig, now: datetime) -> RawItem | None:
    title = clean_text(str(entry.get("title") or ""))
    slug = str(entry.get("slug") or "")
    if not title or not slug:
        return None
    status = entry.get("status") or {}
    if not isinstance(status, dict):
        status = {}
    if status.get("is_finished"):
        return None
    if not status.get("is_registration_available"):
        return None

    dates = entry.get("dates") or {}
    starts_at = parse_iso(dates.get("start_date")) if isinstance(dates, dict) else None
    ends_at = parse_iso(dates.get("final_date")) if isinstance(dates, dict) else None
    if ends_at is not None and ends_at < now:
        return None
    reg_deadline = None

    comp_type = entry.get("type") or {}
    type_name = clean_text(str(comp_type.get("name") or "")) if isinstance(comp_type, dict) else ""
    tags = [str(t) for t in (entry.get("tags") or []) if str(t).strip()]
    if type_name:
        tags.append(type_name)

    participants = entry.get("participants_count")
    duration = clean_text(str(entry.get("duration_days") or ""))
    description_parts = ["ODS.ai."]
    if type_name:
        description_parts.append(f"Тип: {type_name}.")
    if duration:
        description_parts.append(f"Длительность: {duration}.")
    if participants:
        description_parts.append(f"Участников: {participants}.")
    description = clean_text(" ".join(description_parts), 1000)

    return RawItem(
        source_code=config.code,
        external_id=str(entry.get("id") or slug),
        title=title[:600],
        description=description,
        url=f"https://ods.ai/competitions/{slug}",
        starts_at=starts_at,
        ends_at=ends_at,
        reg_deadline=reg_deadline,
        date_confidence="exact" if starts_at else "unknown",
        raw_dates=f"{dates.get('start_date') if isinstance(dates, dict) else ''} - "
        f"{dates.get('final_date') if isinstance(dates, dict) else ''}".strip(" -"),
        format="online",
        city=None,
        country=None,
        language="ru",
        geo=config.geo,
        eligibility="ru",
        tags=tags,
        raw_extra={"type": type_name},
    )
