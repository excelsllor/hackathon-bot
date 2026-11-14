from __future__ import annotations

import logging
import time
from collections.abc import Iterable

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


class AlertManager:
    def __init__(self, settings: Settings, admins: Iterable[int] | None = None) -> None:
        self._token = settings.tg_token
        self._admins = list(admins if admins is not None else settings.tg_admins)
        self._cooldown_seconds = settings.admin_alert_cooldown_minutes * 60
        self._last_sent: dict[str, float] = {}

    async def notify(self, text: str, key: str | None = None) -> None:
        if not self._token or not self._admins:
            logger.warning("admin alert (not configured): %s", text[:500])
            return
        if key:
            now = time.monotonic()
            last = self._last_sent.get(key, 0)
            if now - last < self._cooldown_seconds:
                return
            self._last_sent[key] = now

        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        async with httpx.AsyncClient(timeout=15) as client:
            for admin in self._admins:
                try:
                    response = await client.post(
                        url,
                        json={"chat_id": admin, "text": text[:4000]},
                    )
                    if response.status_code >= 400:
                        logger.warning(
                            "admin alert failed for %s: HTTP %s", admin, response.status_code
                        )
                except httpx.HTTPError as exc:
                    logger.warning("admin alert failed for %s: %s", admin, exc)
