from app.pipeline.normalize import (
    canonicalize_url,
    clean_text,
    detect_language,
    strip_html,
    truncate,
)


def test_canonicalize_removes_tracking_and_fragment():
    url = "https://www.Example.com/path/?utm_source=x&b=2&a=1#frag"
    assert canonicalize_url(url) == "https://example.com/path?a=1&b=2"


def test_canonicalize_keeps_meaningful_query():
    url = "https://example.com/events?page=2&utm_medium=rss"
    assert canonicalize_url(url) == "https://example.com/events?page=2"


def test_strip_html():
    assert strip_html("<p>Hello <b>world</b></p>") == "Hello world"


def test_truncate():
    assert truncate("abcdef", 4) == "abc…"


def test_detect_language():
    assert detect_language("Привет, это хакатон") == "ru"
    assert detect_language("This is a hackathon") == "en"


def test_clean_text_collapses_spaces():
    assert clean_text("  a \n b \t c  ") == "a b c"
