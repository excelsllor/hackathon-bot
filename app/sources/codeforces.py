from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.pipeline.normalize import clean_text
from app.sources.base import RawItem, SourceConfig, SourceError
from app.sources.http import HttpClient

logger = logging.getLogger(__name__)


class CodeforcesAdapter:
    kind = "codeforces_api"

    async def fetch(
        self, config: SourceConfig, http: HttpClient, now: datetime
    ) -> list[RawItem]:
        data = await http.get_json(config.url)
        if not isinstance(data, dict) or data.get("status") != "OK":
            raise SourceError(f"{config.code}: unexpected response")
        contests = data.get("result") or []
        items: list[RawItem] = []
        for entry in contests:
            if not isinstance(entry, dict) or entry.get("phase") != "BEFORE":
                continue
            start_seconds = entry.get("startTimeSeconds")
            if not start_seconds:
                continue
            starts_at = datetime.fromtimestamp(int(start_seconds), tz=timezone.utc)
            duration = int(entry.get("durationSeconds") or 0)
            ends_at = starts_at + timedelta(seconds=duration) if duration else starts_at
            name = clean_text(str(entry.get("name") or ""))
            if not name:
                continue
            items.append(
                RawItem(
                    source_code=config.code,
                    external_id=str(entry.get("id") or ""),
                    title=name[:600],
                    description=clean_text(
                        f"Codeforces. Тип: {entry.get('type') or 'contest'}. "
                        f"Длительность: {duration // 3600} ч.",
                        600,
                    ),
                    url=f"https://codeforces.com/contest/{entry.get('id')}",
                    starts_at=starts_at,
                    ends_at=ends_at,
                    reg_deadline=starts_at,
                    date_confidence="exact",
                    raw_dates=str(entry.get("startTimeSeconds") or ""),
                    format="online",
                    language="en",
                    geo=config.geo,
                    eligibility="worldwide",
                    tags=["programming", "contest"],
                )
            )
        return items
