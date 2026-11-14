from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from urllib.parse import urlencode, urlsplit, urlunsplit

from app.pipeline.dates import parse_period
from app.pipeline.normalize import clean_text, strip_html
from app.sources.base import RawItem, SourceConfig, SourceError
from app.sources.http import HttpClient

logger = logging.getLogger(__name__)


class DevpostAdapter:
    kind = "devpost_api"

    async def fetch(
        self, config: SourceConfig, http: HttpClient, now: datetime
    ) -> list[RawItem]:
        pages = int(config.options.get("pages", 8))
        pages = max(1, min(pages, 30))
        items: list[RawItem] = []
        seen_ids: set[str] = set()

        for page in range(1, pages + 1):
            url = _with_page(config.url, page)
            data = await http.get_json(url)
            if not isinstance(data, dict):
                raise SourceError(f"{config.code}: unexpected JSON shape")
            hackathons = data.get("hackathons") or []
            if not isinstance(hackathons, list):
                raise SourceError(f"{config.code}: hackathons is not a list")
            if not hackathons:
                break
            for entry in hackathons:
                if not isinstance(entry, dict):
                    continue
                external_id = str(entry.get("id") or "")
                if not external_id or external_id in seen_ids:
                    continue
                seen_ids.add(external_id)
                item = _parse_entry(entry, config, now)
                if item:
                    items.append(item)
            if len(hackathons) < 9:
                break
            await asyncio.sleep(0.4)

        return items


def _parse_entry(entry: dict, config: SourceConfig, now: datetime) -> RawItem | None:
    title = clean_text(str(entry.get("title") or ""))
    url = str(entry.get("url") or "").strip()
    if not title or not url:
        return None
    if url.startswith("//"):
        url = "https:" + url

    period = clean_text(str(entry.get("submission_period_dates") or ""))
    dates = parse_period(period, now, language="en")
    starts_at = dates.starts_at
    ends_at = dates.ends_at
    reg_deadline = dates.reg_deadline or ends_at

    if ends_at is not None and ends_at < now - timedelta(days=1):
        return None
    if starts_at is None and ends_at is None:
        return None

    location = entry.get("displayed_location") or {}
    location_text = clean_text(str(location.get("location") or ""))
    icon = str(location.get("icon") or "")
    is_online = icon == "globe" or any(
        token in location_text.lower() for token in ("online", "virtual", "remote")
    )
    fmt = "online" if is_online else ("offline" if location_text else "unknown")
    if config.online_only and fmt != "online":
        return None

    themes = []
    for theme in entry.get("themes") or []:
        if isinstance(theme, dict) and theme.get("name"):
            themes.append(clean_text(str(theme["name"])))

    prize = strip_html(str(entry.get("prize_amount") or ""))
    organization = clean_text(str(entry.get("organization_name") or ""))
    description_parts = []
    if organization:
        description_parts.append(f"Организатор: {organization}.")
    if themes:
        description_parts.append("Темы: " + ", ".join(themes) + ".")
    if prize:
        description_parts.append(f"Призовой фонд: {prize}.")
    if location_text:
        description_parts.append(f"Локация: {location_text}.")
    description = clean_text(" ".join(description_parts), 1200)

    city = None
    country = None
    if not is_online and location_text:
        parts = [p.strip() for p in location_text.split(",") if p.strip()]
        if parts:
            city = parts[0][:128]
            if len(parts) > 1 and len(parts[-1]) <= 3:
                country = parts[-1][:64]

    return RawItem(
        source_code=config.code,
        external_id=str(entry.get("id") or ""),
        title=title[:600],
        description=description,
        url=url,
        starts_at=starts_at,
        ends_at=ends_at,
        reg_deadline=reg_deadline,
        date_confidence=dates.date_confidence if dates.raw_dates else "unknown",
        raw_dates=period,
        format=fmt,
        city=city,
        country=country,
        language="en",
        geo=config.geo,
        eligibility="restricted" if entry.get("invite_only") else "unknown",
        tags=themes,
        raw_extra={"open_state": entry.get("open_state")},
    )


def _with_page(url: str, page: int) -> str:
    parts = urlsplit(url)
    query = parts.query
    extra = urlencode({"page": page})
    query = f"{query}&{extra}" if query else extra
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))
