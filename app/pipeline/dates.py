from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

MSK = ZoneInfo("Europe/Moscow")

MONTHS = {
    "января": 1,
    "январь": 1,
    "янв": 1,
    "февраля": 2,
    "февраль": 2,
    "фев": 2,
    "марта": 3,
    "март": 3,
    "мар": 3,
    "апреля": 4,
    "апрель": 4,
    "апр": 4,
    "мая": 5,
    "май": 5,
    "июня": 6,
    "июнь": 6,
    "июн": 6,
    "июля": 7,
    "июль": 7,
    "июл": 7,
    "августа": 8,
    "август": 8,
    "авг": 8,
    "сентября": 9,
    "сентябрь": 9,
    "сен": 9,
    "сент": 9,
    "октября": 10,
    "октябрь": 10,
    "окт": 10,
    "ноября": 11,
    "ноябрь": 11,
    "ноя": 11,
    "декабря": 12,
    "декабрь": 12,
    "дек": 12,
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}

MONTH_TOKEN = r"[а-яёa-z]{3,}"

_RANGE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        rf"({MONTH_TOKEN})\.?\s+(\d{{1,2}}),?\s+(\d{{4}})\s*[-–—]\s*"
        rf"({MONTH_TOKEN})\.?\s+(\d{{1,2}}),?\s+(\d{{4}})",
        re.I,
    ),
    re.compile(
        rf"(\d{{1,2}})\s+({MONTH_TOKEN})\.?\s+(\d{{4}})\s*[-–—]\s*"
        rf"(\d{{1,2}})\s+({MONTH_TOKEN})\.?\s+(\d{{4}})",
        re.I,
    ),
    re.compile(rf"(\d{{1,2}})\s*[-–—]\s*(\d{{1,2}})\s+({MONTH_TOKEN})\.?\s+(\d{{4}})", re.I),
    re.compile(rf"({MONTH_TOKEN})\.?\s+(\d{{1,2}})\s*[-–—]\s*(\d{{1,2}}),?\s+(\d{{4}})", re.I),
    re.compile(
        rf"({MONTH_TOKEN})\.?\s+(\d{{1,2}})\s*[-–—]\s*({MONTH_TOKEN})\.?\s+(\d{{1,2}}),?\s+(\d{{4}})",
        re.I,
    ),
    re.compile(
        rf"(\d{{1,2}})\s+({MONTH_TOKEN})\.?\s*[-–—]\s*(\d{{1,2}})\s+({MONTH_TOKEN})\.?\s+(\d{{4}})",
        re.I,
    ),
    re.compile(
        r"(\d{1,2})[./](\d{1,2})[./](\d{4})\s*[-–—]\s*(\d{1,2})[./](\d{1,2})[./](\d{4})"
    ),
    re.compile(rf"(\d{{1,2}})\s*[-–—]\s*(\d{{1,2}})\s+({MONTH_TOKEN})(?:\s+(\d{{4}}))?", re.I),
    re.compile(rf"({MONTH_TOKEN})\.?\s+(\d{{1,2}})\s*[-–—]\s*(\d{{1,2}})(?:,?\s+(\d{{4}}))?", re.I),
]

_SINGLE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::\d{2})?)?(Z|[+-]\d{2}:?\d{2})?"),
    re.compile(rf"(\d{{1,2}})\s+({MONTH_TOKEN})\.?\s+(\d{{4}})", re.I),
    re.compile(rf"({MONTH_TOKEN})\.?\s+(\d{{1,2}}),?\s+(\d{{4}})", re.I),
    re.compile(r"(\d{1,2})[./](\d{1,2})[./](\d{4})"),
    re.compile(rf"(\d{{1,2}})\s+({MONTH_TOKEN})(?!\s+\d{{4}})", re.I),
]

_DEADLINE_CONTEXT = re.compile(
    r"(?i)(?:регистрац\w*|заявк\w*|прием заявок|подача заявок|дедлайн|deadline|"
    r"apply by|registration\s+(?:ends|closes|deadline)|register by|"
    r"applications?\s+(?:close|deadline)|entries\s+close|submissions?\s+(?:close|deadline))"
)
_DEADLINE_HINT = re.compile(
    r"(?i)(?:\bдо\b|\buntil\b|\bby\b|\bends?\b|\bcloses?\b|\bclose\b|\bdeadline\b|"
    r"\bзакрыт\w*|\bзаканчива\w*)"
)
_EVENT_CUE = re.compile(
    r"(?i)(?:дата|срок|старт|начало|проведен|состоится|пройдет|пройдёт|"
    r"регистрац|дедлайн|deadline|date|start|end|event|hackathon|хакатон)"
)


@dataclass(slots=True)
class ExtractedRange:
    start: date
    end: date
    span: tuple[int, int]
    raw: str
    confidence: str = "approx"


@dataclass(slots=True)
class ExtractedSingle:
    value: date
    span: tuple[int, int]
    raw: str
    confidence: str = "approx"
    has_time: bool = False
    exact_dt: datetime | None = None


@dataclass(slots=True)
class DateParseResult:
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    reg_deadline: datetime | None = None
    date_confidence: str = "unknown"
    raw_dates: str = ""


def parse_period(
    text: str,
    now: datetime | None = None,
    language: str = "ru",
) -> DateParseResult:
    now = now or datetime.now(timezone.utc)
    raw_text = (text or "").replace("\xa0", " ")
    if not raw_text.strip():
        return DateParseResult()

    normalized = raw_text.replace("ё", "е").replace("Ё", "Е")
    ranges = _extract_ranges(normalized, now)
    singles = _extract_singles(normalized, now)
    singles = [s for s in singles if not _overlaps_any(s.span, [r.span for r in ranges])]

    deadline, deadline_raw = _extract_deadline(normalized, now)
    if deadline:
        # Deadline overlap should not be treated as the event itself.
        ranges = [r for r in ranges if not _overlaps_any(r.span, deadline_raw[1])]
        singles = [s for s in singles if not _overlaps_any(s.span, deadline_raw[1])]

    starts: datetime | None = None
    ends: datetime | None = None
    confidence = "unknown"
    raw_parts: list[str] = []

    if ranges:
        primary = min(ranges, key=lambda r: (r.start, r.end))
        starts = _to_start_dt(primary.start)
        ends = _to_end_dt(primary.end)
        confidence = primary.confidence
        raw_parts.append(primary.raw)

    if starts is None and singles:
        exact = next((s for s in singles if s.exact_dt is not None), None)
        if exact and exact.exact_dt:
            starts = exact.exact_dt.astimezone(timezone.utc)
            ends = starts
            confidence = "exact"
            raw_parts.append(exact.raw)
        else:
            ordered = sorted(singles, key=lambda s: s.value)
            first = ordered[0]
            starts = _to_start_dt(first.value)
            if len(ordered) >= 2 and (ordered[1].value - first.value).days <= 14:
                ends = _to_end_dt(ordered[1].value)
                raw_parts.extend([first.raw, ordered[1].raw])
            else:
                raw_parts.append(first.raw)
            confidence = "approx"

    if deadline:
        raw_parts.append(deadline_raw[0])
        if confidence == "unknown":
            confidence = "approx"

    return DateParseResult(
        starts_at=starts,
        ends_at=ends,
        reg_deadline=deadline,
        date_confidence=confidence,
        raw_dates=" | ".join(dict.fromkeys(p for p in raw_parts if p))[:1000],
    )


def _extract_ranges(text: str, now: datetime) -> list[ExtractedRange]:
    accepted: list[ExtractedRange] = []
    for pattern in _RANGE_PATTERNS:
        for match in pattern.finditer(text):
            if _overlaps_any(match.span(), [r.span for r in accepted]):
                continue
            result = _range_from_match(pattern, match, now)
            if result:
                accepted.append(result)
    return accepted


def _range_from_match(
    pattern: re.Pattern[str], match: re.Match[str], now: datetime
) -> ExtractedRange | None:
    groups = match.groups()
    try:
        if len(groups) == 4:
            g1, g2, g3, g4 = groups
            if _is_month(g1):
                month = _month_num(g1)
                if g4:
                    year = int(g4)
                    start = _safe_date(year, month, int(g2))
                    end = _safe_date(year, month, int(g3))
                else:
                    start = _with_year(int(g2), month, None, now)
                    end = _with_year(int(g3), month, None, now)
            else:
                month = _month_num(g3)
                if g4:
                    year = int(g4)
                    start = _safe_date(year, month, int(g1))
                    end = _safe_date(year, month, int(g2))
                else:
                    start = _with_year(int(g1), month, None, now)
                    end = _with_year(int(g2), month, None, now)
        elif len(groups) == 5:
            g1, g2, g3, g4, g5 = groups
            if _is_month(g1) and _is_month(g3):
                year = int(g5)
                start = _safe_date(year, _month_num(g1), int(g2))
                end = _safe_date(year, _month_num(g3), int(g4))
            else:
                year = int(g5)
                start = _safe_date(year, _month_num(g2), int(g1))
                end = _safe_date(year, _month_num(g4), int(g3))
        elif len(groups) == 6:
            g1, g2, g3, g4, g5, g6 = groups
            if g1.isdigit() and g2.isdigit() and g3.isdigit():
                start = _safe_date(int(g3), int(g2), int(g1))
                end = _safe_date(int(g6), int(g5), int(g4))
            elif g1.isdigit():
                start = _safe_date(int(g3), _month_num(g2), int(g1))
                end = _safe_date(int(g6), _month_num(g5), int(g4))
            else:
                start = _safe_date(int(g3), _month_num(g1), int(g2))
                end = _safe_date(int(g6), _month_num(g4), int(g5))
        elif len(groups) == 3:
            g1, g2, g3 = groups
            if _is_month(g1):
                month = _month_num(g1)
                start = _with_year(int(g2), month, int(g3), now)
                end = _with_year(int(g2), month, int(g3), now)
            else:
                month = _month_num(g3)
                year = _year_for(int(g1), month, now)
                start = _safe_date(year, month, int(g1))
                end = _safe_date(year, month, int(g2))
        else:
            return None
    except (TypeError, ValueError, KeyError):
        return None
    if not start or not end:
        return None
    if end < start:
        if (start - end).days > 300:
            end = end.replace(year=end.year + 1)
        else:
            start, end = end, start
    return ExtractedRange(start=start, end=end, span=match.span(), raw=match.group(0))


def _extract_singles(text: str, now: datetime) -> list[ExtractedSingle]:
    accepted: list[ExtractedSingle] = []
    for pattern in _SINGLE_PATTERNS:
        for match in pattern.finditer(text):
            if _overlaps_any(match.span(), [s.span for s in accepted]):
                continue
            result = _single_from_match(pattern, match, text, now)
            if result:
                accepted.append(result)
    return accepted


def _single_from_match(
    pattern: re.Pattern[str], match: re.Match[str], text: str, now: datetime
) -> ExtractedSingle | None:
    raw = match.group(0)
    groups = match.groups()
    try:
        if pattern.pattern.startswith(r"(\d{4})-"):
            year, month, day = int(groups[0]), int(groups[1]), int(groups[2])
            hour = int(groups[3] or 0)
            minute = int(groups[4] or 0)
            tz_token = groups[5]
            dt = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
            if tz_token and tz_token not in {"Z", "z"}:
                sign = 1 if tz_token[0] == "+" else -1
                clean = tz_token[1:].replace(":", "")
                offset = timedelta(hours=int(clean[:2]), minutes=int(clean[2:] or 0)) * sign
                dt = dt.replace(tzinfo=timezone(offset)).astimezone(timezone.utc)
            return ExtractedSingle(
                value=dt.date(),
                span=match.span(),
                raw=raw,
                confidence="exact",
                has_time=bool(groups[3]),
                exact_dt=dt,
            )
        if len(groups) == 3 and groups[0].isdigit() and groups[1].isdigit():
            day, month, year = int(groups[0]), int(groups[1]), int(groups[2])
            value = _safe_date(year, month, day)
            if not value:
                return None
            return ExtractedSingle(value=value, span=match.span(), raw=raw)
        if len(groups) == 3 and _is_month(groups[0]):
            month = _month_num(groups[0])
            year = int(groups[2])
            value = _safe_date(year, month, int(groups[1]))
        elif len(groups) == 3:
            month = _month_num(groups[1])
            year = int(groups[2])
            value = _safe_date(year, month, int(groups[0]))
        elif len(groups) == 2:
            if not _has_event_cue(text, match.span()):
                return None
            month = _month_num(groups[1])
            value = _with_year(int(groups[0]), month, None, now)
        else:
            return None
    except (TypeError, ValueError, KeyError):
        return None
    if not value:
        return None
    return ExtractedSingle(value=value, span=match.span(), raw=raw)


def _extract_deadline(text: str, now: datetime) -> tuple[datetime | None, tuple[str, list[tuple[int, int]]]]:
    for match in _DEADLINE_CONTEXT.finditer(text):
        start = max(0, match.start() - 50)
        end = min(len(text), match.end() + 180)
        context = text[start:end]
        if not _DEADLINE_HINT.search(context):
            continue
        ranges = _extract_ranges(context, now)
        singles = _extract_singles(context, now)
        best_range = min(
            ranges,
            key=lambda value: abs((value.span[0] + start) - match.end()),
            default=None,
        )
        best_single = min(
            singles,
            key=lambda value: abs((value.span[0] + start) - match.end()),
            default=None,
        )
        if best_range is not None and (
            best_single is None
            or abs((best_range.span[0] + start) - match.end())
            <= abs((best_single.span[0] + start) - match.end())
        ):
            span = (best_range.span[0] + start, best_range.span[1] + start)
            return _to_end_dt(best_range.end), (best_range.raw, [span])
        if best_single is not None:
            span = (best_single.span[0] + start, best_single.span[1] + start)
            return _to_end_dt(best_single.value), (best_single.raw, [span])
    return None, ("", [])


def _has_event_cue(text: str, span: tuple[int, int]) -> bool:
    start = max(0, span[0] - 80)
    end = min(len(text), span[1] + 80)
    return bool(_EVENT_CUE.search(text[start:end]))


def _overlaps_any(span: tuple[int, int], others: Iterable[tuple[int, int]]) -> bool:
    return any(span[0] < other[1] and other[0] < span[1] for other in others)


def _is_month(token: str) -> bool:
    return token.lower().strip(".") in MONTHS


def _month_num(token: str) -> int:
    return MONTHS[token.lower().strip(".")]


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _year_for(day: int, month: int, now: datetime) -> int:
    now_msk = now.astimezone(MSK)
    candidate = _safe_date(now_msk.year, month, day)
    if candidate and candidate < (now_msk.date() - timedelta(days=60)):
        return now_msk.year + 1
    return now_msk.year


def _with_year(day: int, month: int, year: int | None, now: datetime) -> date | None:
    if year:
        return _safe_date(year, month, day)
    return _safe_date(_year_for(day, month, now), month, day)


def _to_start_dt(value: date) -> datetime:
    return datetime.combine(value, time(0, 0), tzinfo=MSK).astimezone(timezone.utc)


def _to_end_dt(value: date) -> datetime:
    return datetime.combine(value, time(23, 59, 59), tzinfo=MSK).astimezone(timezone.utc)


def deadline_to_end_of_day(value: datetime) -> datetime:
    local = value.astimezone(MSK)
    return datetime.combine(local.date(), time(23, 59, 59), tzinfo=MSK).astimezone(timezone.utc)


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
