from __future__ import annotations

import logging
from datetime import datetime
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from app.pipeline.classify import HACKATHON_CUE
from app.pipeline.dates import parse_period
from app.pipeline.normalize import clean_text
from app.sources.base import RawItem, SourceConfig
from app.sources.http import HttpClient

logger = logging.getLogger(__name__)

BASE_URL = "https://fsp-russia.ru"


class FspCalendarAdapter:
    kind = "fsp_calendar"

    async def fetch(
        self, config: SourceConfig, http: HttpClient, now: datetime
    ) -> list[RawItem]:
        html, _ = await http.get_text(config.url)
        tree = HTMLParser(html)
        require_lipetsk = bool(config.options.get("require_lipetsk", False))
        items: list[RawItem] = []

        for card in tree.css(".calendar-table__event-item"):
            name_node = card.css_first(".name p")
            date_node = card.css_first(".date p")
            if name_node is None or date_node is None:
                continue
            title = clean_text(name_node.text(strip=True))
            card_text = clean_text(card.text(separator=" ", strip=True))
            if not title:
                continue
            if require_lipetsk and "ипец" not in card_text:
                continue
            if not HACKATHON_CUE.search(card_text) and not _is_sport_programming(card_text):
                continue

            dates = parse_period(date_node.text(strip=True), now, language="ru")
            if dates.starts_at is None:
                continue

            location_node = card.css_first(".location p")
            online_node = card.css_first(".online p")
            discipline_nodes = card.css(".discipline .info p")
            participants = ""
            discipline = ""
            for info in discipline_nodes:
                value = clean_text(info.text(strip=True))
                prev = info.parent.parent.css_first(".name p") if info.parent and info.parent.parent else None
                label = clean_text(prev.text(strip=True)).lower() if prev else ""
                if "участ" in label:
                    participants = value
                elif "дисциплин" in label:
                    discipline = value

            link_node = card.css_first("a[href]")
            if link_node is None:
                continue
            url = urljoin(BASE_URL, link_node.attributes.get("href", ""))
            location = clean_text(location_node.text(strip=True)) if location_node else ""
            online_text = clean_text(online_node.text(strip=True)) if online_node else ""
            fmt = "online" if "онлайн" in (online_text + " " + location).lower() else "offline"

            description_parts = []
            if discipline:
                description_parts.append(f"Дисциплина: {discipline}.")
            if participants:
                description_parts.append(f"Участники: {participants}.")
            if location:
                description_parts.append(f"Место: {location}.")
            description = clean_text(" ".join(description_parts), 1000)

            items.append(
                RawItem(
                    source_code=config.code,
                    external_id=card.attributes.get("data-id"),
                    title=title[:600],
                    description=description,
                    url=url,
                    starts_at=dates.starts_at,
                    ends_at=dates.starts_at,
                    reg_deadline=None,
                    date_confidence=dates.date_confidence,
                    raw_dates=date_node.text(strip=True),
                    format=fmt,
                    city=location or None,
                    language="ru",
                    geo=config.geo,
                    eligibility="ru",
                    tags=[discipline] if discipline else [],
                )
            )
        return items


def _is_sport_programming(text: str) -> bool:
    lowered = text.lower()
    return "спортивн" in lowered or "программир" in lowered
