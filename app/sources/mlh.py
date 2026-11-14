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


class MlhAdapter:
    kind = "mlh_html"

    async def fetch(
        self, config: SourceConfig, http: HttpClient, now: datetime
    ) -> list[RawItem]:
        html, _ = await http.get_text(config.url)
        tree = HTMLParser(html)
        node = tree.css_first("script[data-page]")
        if node is None:
            raise SourceError(f"{config.code}: data-page not found")
        try:
            data = json.loads(node.text())
        except ValueError as exc:
            raise SourceError(f"{config.code}: invalid data-page JSON") from exc
        props = data.get("props", {}) if isinstance(data, dict) else {}
        events = props.get("upcomingEvents") or []

        items: list[RawItem] = []
        for entry in events:
            if not isinstance(entry, dict):
                continue
            name = clean_text(str(entry.get("name") or ""))
            if not name:
                continue
            fmt_type = str(entry.get("formatType") or "")
            is_online = fmt_type == "digital"
            if config.online_only and not is_online:
                continue
            starts_at = parse_iso(entry.get("startsAt"))
            ends_at = parse_iso(entry.get("endsAt"))
            if ends_at is not None and ends_at < now:
                continue
            location = clean_text(str(entry.get("location") or ""))
            website = str(entry.get("websiteUrl") or "").strip()
            internal = str(entry.get("url") or "").strip()
            url = website or (f"https://events.mlh.io{internal}" if internal else "")
            if not url:
                continue
            focus = []
            custom = entry.get("customFields") or {}
            if isinstance(custom, dict):
                focus = [str(x) for x in (custom.get("hackathon_focus") or []) if str(x).strip()]
            items.append(
                RawItem(
                    source_code=config.code,
                    external_id=str(entry.get("id") or entry.get("slug") or ""),
                    title=name[:600],
                    description=clean_text(
                        f"MLH. {location}. " + ("Онлайн." if is_online else "Офлайн."), 800
                    ),
                    url=url,
                    starts_at=starts_at,
                    ends_at=ends_at,
                    reg_deadline=None,
                    date_confidence="exact" if starts_at else "unknown",
                    raw_dates=f"{entry.get('startsAt') or ''} - {entry.get('endsAt') or ''}".strip(
                        " -"
                    ),
                    format="online" if is_online else "offline",
                    city=location or None,
                    country=None,
                    language="en",
                    geo=config.geo,
                    eligibility="unknown",
                    tags=focus,
                )
            )
        return items
