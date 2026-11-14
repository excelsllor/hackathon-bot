from __future__ import annotations

import hashlib
import html
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PREFIXES = ("utm_", "yclid", "fbclid", "gclid", "mc_cid", "mc_eid")
TRACKING_KEYS = {
    "ref",
    "ref_src",
    "source",
    "from",
    "spm",
    "cmp",
    "campaign",
    "s",
    "share",
}
_WS_RE = re.compile(r"\s+")
_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(value: str | None) -> str:
    if not value:
        return ""
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", value)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    return clean_text(text)


def clean_text(value: str | None, max_chars: int | None = None) -> str:
    if not value:
        return ""
    text = html.unescape(str(value))
    text = text.replace("\xa0", " ").replace("\u200b", "")
    text = _WS_RE.sub(" ", text).strip()
    if max_chars and len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text


def normalize_for_match(value: str) -> str:
    text = value.lower().replace("ё", "е")
    return _WS_RE.sub(" ", text).strip()


def detect_language(text: str, default: str = "ru") -> str:
    if not text:
        return default
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return default
    cyrillic = sum(1 for c in letters if "\u0400" <= c <= "\u04ff")
    return "ru" if cyrillic / len(letters) >= 0.2 else "en"


def canonicalize_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return url
    scheme = parts.scheme.lower()
    host = parts.hostname.lower()
    if host.startswith("www."):
        host = host[4:]
    port = parts.port
    netloc = host
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{host}:{port}"
    query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        lower = key.lower()
        if lower in TRACKING_KEYS or any(lower.startswith(p) for p in TRACKING_PREFIXES):
            continue
        query.append((key, value))
    query.sort()
    path = re.sub(r"/+", "/", parts.path or "/")
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    return urlunsplit((scheme, netloc, path, urlencode(query), ""))


def content_hash(*parts: str) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update((part or "").encode("utf-8", "replace"))
        h.update(b"\x00")
    return h.hexdigest()


def truncate(value: str, limit: int) -> str:
    value = clean_text(value)
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def title_key(value: str) -> str:
    text = normalize_for_match(value)
    text = re.sub(r"[^0-9a-zа-я]+", " ", text)
    return _WS_RE.sub(" ", text).strip()
