from pathlib import Path

from app.pipeline.eligibility import (
    assess_eligibility,
    is_visible,
    load_eligibility,
)

CONFIG = load_eligibility(Path(__file__).resolve().parents[1] / "config" / "eligibility.yaml")


def test_us_only_is_restricted():
    status = assess_eligibility(
        "Open to residents of the United States only.", CONFIG, geo="intl", format="online"
    )
    assert status == "restricted"
    assert not is_visible(status, geo="intl", strict=False)


def test_russia_exclusion_is_restricted():
    status = assess_eligibility(
        "This hackathon is not open to residents of Russia.", CONFIG, geo="intl", format="online"
    )
    assert status == "restricted"


def test_worldwide():
    status = assess_eligibility(
        "Open to all countries worldwide.", CONFIG, geo="intl", format="online"
    )
    assert status == "worldwide"
    assert is_visible(status, geo="intl", strict=True)


def test_online_unknown():
    status = assess_eligibility(
        "Online hackathon for developers.", CONFIG, geo="intl", format="online"
    )
    assert status == "online_open"


def test_check_badge():
    status = assess_eligibility(
        "Void where prohibited. See eligibility requirements.", CONFIG, geo="intl", format="online"
    )
    assert status == "check"
    assert is_visible(status, geo="intl", strict=False)
    assert not is_visible(status, geo="intl", strict=True)


def test_russian_event():
    status = assess_eligibility("Онлайн-хакатон для разработчиков.", CONFIG, geo="ru", format="online")
    assert status == "ru"
    assert is_visible(status, geo="ru", strict=True)


def test_invite_only_hint():
    status = assess_eligibility(
        "Online event.", CONFIG, geo="intl", format="online", hint="invite_only"
    )
    assert status == "restricted"


def test_schoolchildren_event_is_restricted():
    status = assess_eligibility(
        "Хакатон для школьников 1-6 классов.",
        CONFIG,
        geo="ru",
        format="offline",
    )
    assert status == "restricted"
    assert not is_visible(status, geo="ru", strict=False)
