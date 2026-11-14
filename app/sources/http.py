from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

from app.sources.base import SourceError

logger = logging.getLogger(__name__)


class HttpClient:
    def __init__(
        self,
        user_agent: str,
        timeout_seconds: float = 20.0,
        max_bytes: int = 8_000_000,
    ) -> None:
        self.max_bytes = max_bytes
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=True,
            max_redirects=3,
            headers={
                "User-Agent": user_agent,
                "Accept": "text/html,application/json,application/xml,text/xml,"
                "application/rss+xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ru,en;q=0.8",
            },
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def get_bytes(
        self,
        url: str,
        *,
        max_bytes: int | None = None,
        attempts: int = 3,
    ) -> tuple[bytes, str]:
        limit = max_bytes or self.max_bytes
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                async with self._client.stream("GET", url) as response:
                    if response.status_code == 429 or response.status_code >= 500:
                        raise httpx.HTTPStatusError(
                            f"HTTP {response.status_code}",
                            request=response.request,
                            response=response,
                        )
                    if response.status_code >= 400:
                        raise SourceError(f"HTTP {response.status_code} for {url}")
                    content_type = response.headers.get("content-type", "")
                    chunks = bytearray()
                    async for chunk in response.aiter_bytes():
                        chunks.extend(chunk)
                        if len(chunks) > limit:
                            raise SourceError(f"response too large for {url}")
                    return bytes(chunks), content_type
            except httpx.HTTPStatusError as exc:
                last_error = exc
                status = exc.response.status_code
                if 400 <= status < 500 and status != 429:
                    raise SourceError(f"HTTP {status} for {url}") from exc
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_error = exc
            except SourceError:
                raise
            if attempt < attempts:
                await asyncio.sleep(min(2 ** attempt, 8))
        raise SourceError(f"fetch failed for {url}: {last_error}") from last_error

    async def get_text(self, url: str, *, max_bytes: int | None = None) -> tuple[str, str]:
        data, content_type = await self.get_bytes(url, max_bytes=max_bytes)
        charset = _charset_from_content_type(content_type)
        encoding = charset or "utf-8"
        try:
            text = data.decode(encoding, errors="replace")
        except LookupError:
            text = data.decode("utf-8", errors="replace")
        if "\ufffd" in text and encoding.lower() not in {"utf-8", "utf8"}:
            text = data.decode("utf-8", errors="replace")
        return text, content_type

    async def get_json(self, url: str, *, max_bytes: int | None = None) -> Any:
        text, _ = await self.get_text(url, max_bytes=max_bytes)
        try:
            return json.loads(text)
        except ValueError as exc:
            raise SourceError(f"invalid JSON from {url}") from exc


def _charset_from_content_type(content_type: str) -> str | None:
    for part in content_type.split(";")[1:]:
        key, _, value = part.strip().partition("=")
        if key.lower() == "charset" and value:
            return value.strip().strip('"').strip("'")
    return None
