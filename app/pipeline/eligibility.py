from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from app.pipeline.normalize import normalize_for_match


@dataclass(slots=True)
class EligibilityConfig:
    restricted: list[re.Pattern[str]]
    check: list[re.Pattern[str]]
    worldwide: list[re.Pattern[str]]
    online: list[re.Pattern[str]]
    offline: list[re.Pattern[str]]


def load_eligibility(path: str | Path) -> EligibilityConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return EligibilityConfig(
        restricted=_compile_many(raw.get("restricted_patterns")),
        check=_compile_many(raw.get("check_patterns")),
        worldwide=_compile_many(raw.get("worldwide_patterns")),
        online=_compile_many(raw.get("online_patterns")),
        offline=_compile_many(raw.get("offline_patterns")),
    )


def assess_eligibility(
    text: str,
    config: EligibilityConfig,
    *,
    geo: str,
    format: str,
    hint: str = "",
) -> str:
    normalized = normalize_for_match(text)
    if hint == "invite_only":
        return "restricted"
    if any(p.search(normalized) for p in config.restricted):
        return "restricted"
    if geo == "ru":
        if any(p.search(normalized) for p in config.check):
            return "check"
        return "ru"
    if any(p.search(normalized) for p in config.worldwide):
        return "worldwide"
    if any(p.search(normalized) for p in config.check):
        return "check"
    if format == "online":
        return "online_open"
    return "unknown"


def is_visible(status: str, *, geo: str, strict: bool) -> bool:
    if status == "restricted":
        return False
    if geo == "ru":
        return True
    if strict:
        return status in {"worldwide", "online_open"}
    return True


def eligibility_badge(status: str) -> str:
    return {
        "worldwide": "🌍 Открыт миру",
        "online_open": "🌐 Онлайн, доступен из РФ",
        "check": "⚠️ Проверь правила",
        "restricted": "⛔ Ограничен",
        "ru": "🇷🇺 РФ",
        "unknown": "❓ Уточни правила",
    }.get(status, "❓ Уточни правила")


def _compile_many(values: object) -> list[re.Pattern[str]]:
    if not isinstance(values, list):
        return []
    result: list[re.Pattern[str]] = []
    for value in values:
        if isinstance(value, str) and value.strip():
            result.append(re.compile(value, re.IGNORECASE | re.UNICODE))
    return result
