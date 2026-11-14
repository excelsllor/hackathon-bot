from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml


class SourceError(RuntimeError):
    """A source could not be fetched or parsed."""


@dataclass(slots=True)
class SourceConfig:
    code: str
    name: str
    kind: str
    url: str
    enabled: bool = True
    interval_minutes: int = 180
    priority: int = 50
    geo: str = "intl"
    region: str = ""
    language: str = "en"
    online_only: bool = False
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RawItem:
    source_code: str
    title: str
    url: str
    description: str = ""
    external_id: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    reg_deadline: datetime | None = None
    date_confidence: str = "unknown"
    raw_dates: str = ""
    format: str = "unknown"
    city: str | None = None
    country: str | None = None
    language: str = "en"
    geo: str = "intl"
    eligibility: str = "unknown"
    tags: list[str] = field(default_factory=list)
    raw_extra: dict[str, Any] = field(default_factory=dict)


_ALLOWED_KINDS = {
    "devpost_api",
    "devfolio_html",
    "ods_html",
    "habr_rss",
    "telegram_public",
    "mlh_html",
    "codeforces_api",
    "codenrock_api",
    "rss_generic",
    "fsp_calendar",
}


def load_sources(path: str | Path) -> list[SourceConfig]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    entries = raw.get("sources")
    if not isinstance(entries, list) or not entries:
        raise SourceError(f"{path}: no sources configured")

    result: list[SourceConfig] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise SourceError("source entry must be a mapping")
        code = str(entry.get("code", "")).strip()
        if not re.fullmatch(r"[a-z0-9_]{2,64}", code):
            raise SourceError(f"invalid source code: {code!r}")
        if code in seen:
            raise SourceError(f"duplicate source code: {code}")
        seen.add(code)

        kind = str(entry.get("kind", "")).strip()
        if kind not in _ALLOWED_KINDS:
            raise SourceError(f"{code}: unsupported kind {kind!r}")

        url = str(entry.get("url", "")).strip()
        if not _is_safe_source_url(url):
            raise SourceError(f"{code}: unsafe or invalid url {url!r}")

        geo = str(entry.get("geo", "intl"))
        if geo not in {"ru", "intl"}:
            raise SourceError(f"{code}: geo must be ru or intl")

        options = entry.get("options") or {}
        if not isinstance(options, dict):
            raise SourceError(f"{code}: options must be a mapping")

        result.append(
            SourceConfig(
                code=code,
                name=str(entry.get("name", code)),
                kind=kind,
                url=url,
                enabled=bool(entry.get("enabled", True)),
                interval_minutes=int(entry.get("interval_minutes", 180)),
                priority=int(entry.get("priority", 50)),
                geo=geo,
                region=str(entry.get("region", "")),
                language=str(entry.get("language", "en")),
                online_only=bool(entry.get("online_only", False)),
                options=options,
            )
        )
    result.sort(key=lambda s: (s.priority, s.code))
    return result


def _is_safe_source_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )
