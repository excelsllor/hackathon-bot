from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

from app.pipeline.dates import parse_iso
from app.pipeline.normalize import clean_text, strip_html
from app.sources.base import RawItem, SourceConfig, SourceError
from app.sources.http import HttpClient

logger = logging.getLogger(__name__)

ALLOWED_TYPES = {
    "hackathon",
    "challenge",
    "ctf",
    "sport_programming",
    "ml_ai",
}

EXCLUDE_TITLE = re.compile(
    r"(?i)(стажировк\w*|стажер\w*|стажёр\w*|акселератор\w*|грант\w*|"
    r"митап\w*|вебинар\w*|обучени\w*|курс\w*|лектори\w*)"
)


class CodenrockAdapter:
    kind = "codenrock_api"

    async def fetch(
        self, config: SourceConfig, http: HttpClient, now: datetime
    ) -> list[RawItem]:
        data = await http.get_json(config.url)
        if not isinstance(data, dict):
            raise SourceError(f"{config.code}: unexpected JSON shape")
        contests = data.get("contests") or []
        if not isinstance(contests, list):
            raise SourceError(f"{config.code}: contests is not a list")

        items: list[RawItem] = []
        for entry in contests:
            if not isinstance(entry, dict):
                continue
            item = _parse_entry(entry, config, now)
            if item is not None:
                items.append(item)
        return items


def _parse_entry(entry: dict, config: SourceConfig, now: datetime) -> RawItem | None:
    title = clean_text(str(entry.get("name") or ""))
    slug = str(entry.get("slug") or "").strip()
    if not title or not slug:
        return None
    if str(entry.get("status") or "").lower() != "active":
        return None
    if EXCLUDE_TITLE.search(title):
        return None
    event_type = str(entry.get("type") or "").lower()
    if event_type not in ALLOWED_TYPES:
        return None

    starts_at = parse_iso(entry.get("startDate"))
    ends_at = parse_iso(entry.get("endDate"))
    reg_start = parse_iso(entry.get("startRegistration"))
    reg_end = parse_iso(entry.get("endRegistration"))
    if reg_end is not None:
        reg_deadline = reg_end
    elif starts_at is not None and starts_at > now:
        reg_deadline = starts_at
    else:
        reg_deadline = None
    if ends_at is not None and ends_at < now - timedelta(days=1):
        return None
    if reg_deadline is not None and reg_deadline < now - timedelta(days=1):
        return None
    if starts_at is None and ends_at is None:
        return None

    description = strip_html(str(entry.get("description") or ""))
    prizes = clean_text(str(entry.get("prizes") or ""))
    team_limit = entry.get("teamLimit")
    min_team = entry.get("minTeamLimit")
    team_note = clean_text(str(entry.get("teamSizeNote") or ""))

    parts = [description] if description else []
    if prizes:
        parts.append(f"Призы: {prizes}.")
    if team_limit:
        if min_team:
            parts.append(f"Команда: от {min_team} до {team_limit} человек.")
        else:
            parts.append(f"Команда: до {team_limit} человек.")
    if team_note:
        parts.append(team_note)
    full_description = clean_text(" ".join(parts), 1500)

    fmt = _detect_format(entry, full_description)
    tags = [event_type] if event_type else []

    return RawItem(
        source_code=config.code,
        external_id=str(entry.get("id") or slug),
        title=title[:600],
        description=full_description,
        url=f"https://codenrock.com/contests/{slug}",
        starts_at=starts_at,
        ends_at=ends_at,
        reg_deadline=reg_deadline,
        date_confidence="exact" if starts_at else "unknown",
        raw_dates=" | ".join(
            value
            for value in (
                f"reg {reg_start.isoformat()}" if reg_start else "",
                f"reg_end {reg_end.isoformat()}" if reg_end else "",
                starts_at.isoformat() if starts_at else "",
                ends_at.isoformat() if ends_at else "",
            )
            if value
        ),
        format=fmt,
        city=None,
        country=None,
        language="ru",
        geo=config.geo,
        eligibility="ru",
        tags=tags,
        raw_extra={"prizes": prizes, "event_type": event_type},
    )


def _detect_format(entry: dict, description: str) -> str:
    if str(entry.get("contest_type") or "").lower() == "remote":
        return "online"
    text = f"{entry.get('name') or ''} {description}".lower()
    online = any(
        token in text for token in ("онлайн", "online", "дистанц", "удаленн", "вебинар")
    )
    offline = any(
        token in text
        for token in ("очно", "офлайн", "offline", "очный", "финал в", "площадк", "город")
    )
    if online and offline:
        return "hybrid"
    if online:
        return "online"
    if offline:
        return "offline"
    return "unknown"
