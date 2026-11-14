from datetime import datetime, timezone

from app.pipeline.normalize import canonicalize_url
from app.sources.base import SourceConfig
from app.sources.codenrock import _parse_entry as parse_codenrock
from app.sources.devfolio import _parse_entry as parse_devfolio
from app.sources.devpost import _parse_entry as parse_devpost
from app.sources.ods import _parse_entry as parse_ods

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)


def _config(kind: str, geo: str = "intl", online_only: bool = True) -> SourceConfig:
    return SourceConfig(
        code=kind,
        name=kind,
        kind=kind,
        url="https://example.com",
        geo=geo,
        online_only=online_only,
    )


def test_devpost_entry():
    item = parse_devpost(
        {
            "id": 1,
            "title": "PayPal AI Hackathon",
            "url": "https://paypalaihackathon.devpost.com/",
            "open_state": "open",
            "submission_period_dates": "Oct 01 - Nov 12, 2026",
            "displayed_location": {"icon": "globe", "location": "Online"},
            "themes": [{"name": "Machine Learning/AI"}, {"name": "Web"}],
            "organization_name": "PayPal",
            "prize_amount": "$<span>67,500</span>",
        },
        _config("devpost_api"),
        NOW,
    )
    assert item is not None
    assert item.format == "online"
    assert item.starts_at is not None
    assert item.reg_deadline is not None
    assert item.tags == ["Machine Learning/AI", "Web"]
    assert "PayPal" in item.description


def test_devpost_offline_is_filtered_for_intl():
    item = parse_devpost(
        {
            "id": 2,
            "title": "NYC Hackathon",
            "url": "https://example.devpost.com/",
            "open_state": "open",
            "submission_period_dates": "Nov 01 - Dec 01, 2026",
            "displayed_location": {"icon": "map-marker", "location": "New York, NY, US"},
            "themes": [{"name": "Web"}],
        },
        _config("devpost_api"),
        NOW,
    )
    assert item is None


def test_devfolio_entry():
    item = parse_devfolio(
        {
            "uuid": "abc",
            "slug": "hackodisha-6.0",
            "name": "HackOdisha 6.0",
            "starts_at": "2026-12-11T16:30:00+00:00",
            "ends_at": "2026-12-13T06:30:00+00:00",
            "is_online": True,
            "settings": {"reg_ends_at": "2026-12-10T18:29:00+00:00"},
            "themes": [{"theme": {"name": "AI"}}],
            "participants_count": 100,
        },
        _config("devfolio_html"),
        NOW,
    )
    assert item is not None
    assert item.format == "online"
    assert item.url == "https://devfolio.co/hackathons/hackodisha-6.0"
    assert item.reg_deadline is not None
    assert item.tags == ["AI"]


def test_ods_entry():
    item = parse_ods(
        {
            "id": "uuid",
            "title": "Avito ML cup 2026",
            "slug": "avitotechrecsys2026",
            "dates": {
                "start_date": "2026-10-28T09:00:00Z",
                "final_date": "2026-12-09T09:00:00Z",
            },
            "status": {
                "is_finished": False,
                "is_registration_available": True,
            },
            "type": {"name": "Predictions"},
            "tags": [],
            "participants_count": 1193,
            "duration_days": "42 дня",
        },
        _config("ods_html", geo="ru", online_only=False),
        NOW,
    )
    assert item is not None
    assert item.format == "online"
    assert item.geo == "ru"
    assert item.url == "https://ods.ai/competitions/avitotechrecsys2026"


def test_codenrock_entry():
    item = parse_codenrock(
        {
            "id": "7c33795e-d808-434f-8ac0-23d96b833ef4",
            "slug": "hahaton-3d-2026",
            "name": "Хахатон в 3D 2026",
            "description": "Онлайн-хакатон по созданию 3D-игр.",
            "type": "hackathon",
            "contest_type": "remote",
            "status": "active",
            "startRegistration": "2026-10-21T15:00:00.000Z",
            "endRegistration": "2026-12-23T09:00:00.000Z",
            "startDate": "2026-12-01T16:00:00.000Z",
            "endDate": "2027-01-28T13:00:00.000Z",
            "prizes": "Призы за 1–3 места",
            "teamLimit": 2,
            "minTeamLimit": 1,
        },
        _config("codenrock_api", geo="ru", online_only=False),
        NOW,
    )
    assert item is not None
    assert item.url == "https://codenrock.com/contests/hahaton-3d-2026"
    assert item.format == "online"
    assert item.geo == "ru"
    assert item.reg_deadline is not None
    assert item.tags == ["hackathon"]
    assert "Призы" in item.description


def test_codenrock_challenge_entry():
    item = parse_codenrock(
        {
            "id": "challenge-1",
            "slug": "ai-challenge-2026",
            "name": "AI Challenge 2026",
            "description": "Онлайн-чемпионат по ML.",
            "type": "challenge",
            "contest_type": "remote",
            "status": "active",
            "startDate": "2026-12-01T00:00:00Z",
            "endDate": "2026-12-15T00:00:00Z",
        },
        _config("codenrock_api", geo="ru", online_only=False),
        NOW,
    )
    assert item is not None
    assert item.tags == ["challenge"]
    assert item.url == "https://codenrock.com/contests/ai-challenge-2026"


def test_codenrock_filters_meetups_and_finished():
    base = {
        "id": "1",
        "slug": "event",
        "name": "Event",
        "description": "",
        "type": "hackathon",
        "contest_type": "remote",
        "status": "active",
        "startDate": "2026-12-01T00:00:00Z",
        "endDate": "2026-12-02T00:00:00Z",
    }
    assert (
        parse_codenrock(
            {**base, "type": "meetup"},
            _config("codenrock_api", geo="ru", online_only=False),
            NOW,
        )
        is None
    )
    assert (
        parse_codenrock(
            {**base, "status": "finished"},
            _config("codenrock_api", geo="ru", online_only=False),
            NOW,
        )
        is None
    )


def test_unknown_urls_are_stable():
    assert canonicalize_url("https://example.com/") == "https://example.com/"
    assert canonicalize_url("//example.com/x") == "https://example.com/x"
