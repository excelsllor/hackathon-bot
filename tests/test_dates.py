from datetime import datetime, timezone

from app.pipeline.dates import parse_period

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)


def test_devpost_period():
    result = parse_period("Oct 01 - Nov 12, 2026", NOW, "en")
    assert result.starts_at == datetime(2026, 9, 30, 21, 0, tzinfo=timezone.utc)
    assert result.ends_at == datetime(2026, 11, 12, 20, 59, 59, tzinfo=timezone.utc)
    assert result.date_confidence == "approx"


def test_short_range_with_year():
    result = parse_period("Nov 11 - 30, 2026", NOW, "en")
    assert result.starts_at == datetime(2026, 11, 10, 21, 0, tzinfo=timezone.utc)
    assert result.ends_at == datetime(2026, 11, 30, 20, 59, 59, tzinfo=timezone.utc)


def test_yearless_range_prefers_future():
    result = parse_period("Jun 13 - 14", NOW, "en")
    assert result.starts_at == datetime(2027, 6, 12, 21, 0, tzinfo=timezone.utc)
    assert result.ends_at == datetime(2027, 6, 14, 20, 59, 59, tzinfo=timezone.utc)


def test_russian_range():
    result = parse_period("8–11 декабря 2026", NOW, "ru")
    assert result.starts_at == datetime(2026, 12, 7, 21, 0, tzinfo=timezone.utc)
    assert result.ends_at == datetime(2026, 12, 11, 20, 59, 59, tzinfo=timezone.utc)


def test_russian_single_date():
    result = parse_period("12 декабря 2026", NOW, "ru")
    assert result.starts_at == datetime(2026, 12, 11, 21, 0, tzinfo=timezone.utc)


def test_deadline_explicit():
    result = parse_period("Регистрация до 20 декабря 2026", NOW, "ru")
    assert result.reg_deadline == datetime(2026, 12, 20, 20, 59, 59, tzinfo=timezone.utc)
    assert result.starts_at is None


def test_numeric_range():
    result = parse_period("15.12.2026 - 17.12.2026", NOW, "ru")
    assert result.starts_at == datetime(2026, 12, 14, 21, 0, tzinfo=timezone.utc)
    assert result.ends_at == datetime(2026, 12, 17, 20, 59, 59, tzinfo=timezone.utc)


def test_deadline_is_nearest_date():
    result = parse_period("Deadline: May 15, 2026. Event June 1-2, 2026", NOW, "en")
    assert result.reg_deadline == datetime(2026, 5, 15, 20, 59, 59, tzinfo=timezone.utc)
    assert result.starts_at == datetime(2026, 5, 31, 21, 0, tzinfo=timezone.utc)
    assert result.ends_at == datetime(2026, 6, 2, 20, 59, 59, tzinfo=timezone.utc)


def test_event_and_deadline_in_russian_text():
    result = parse_period(
        "Хакатон пройдёт 8–11 декабря. Регистрация до 1 декабря.", NOW, "ru"
    )
    assert result.starts_at == datetime(2026, 12, 7, 21, 0, tzinfo=timezone.utc)
    assert result.ends_at == datetime(2026, 12, 11, 20, 59, 59, tzinfo=timezone.utc)
    assert result.reg_deadline == datetime(2026, 12, 1, 20, 59, 59, tzinfo=timezone.utc)


def test_iso_exact():
    result = parse_period("2026-11-13T21:30:00Z", NOW, "en")
    assert result.starts_at == datetime(2026, 11, 13, 21, 30, tzinfo=timezone.utc)
    assert result.date_confidence == "exact"


def test_cross_year_pair():
    result = parse_period("Dec 30, 2026 - Jan 2, 2027", NOW, "en")
    assert result.starts_at == datetime(2026, 12, 29, 21, 0, tzinfo=timezone.utc)
    assert result.ends_at == datetime(2027, 1, 2, 20, 59, 59, tzinfo=timezone.utc)


def test_full_dates_range():
    result = parse_period("Nov 11, 2026 - Jan 11, 2027", NOW, "en")
    assert result.starts_at == datetime(2026, 11, 10, 21, 0, tzinfo=timezone.utc)
    assert result.ends_at == datetime(2027, 1, 11, 20, 59, 59, tzinfo=timezone.utc)


def test_full_dates_range_russian():
    result = parse_period("11 ноября 2026 - 11 января 2027", NOW, "ru")
    assert result.starts_at == datetime(2026, 11, 10, 21, 0, tzinfo=timezone.utc)
    assert result.ends_at == datetime(2027, 1, 11, 20, 59, 59, tzinfo=timezone.utc)


def test_unknown_date():
    result = parse_period("Просто текст без дат", NOW, "ru")
    assert result.starts_at is None
    assert result.reg_deadline is None
    assert result.date_confidence == "unknown"
