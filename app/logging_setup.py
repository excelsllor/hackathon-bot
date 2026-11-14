from __future__ import annotations

import logging
import sys


class RedactingFilter(logging.Filter):
    """Removes configured secrets from log messages before they are written."""

    def __init__(self, secrets: list[str]) -> None:
        super().__init__()
        self._secrets = [s for s in secrets if s and len(s) >= 6]

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - defensive
            return True
        changed = False
        for secret in self._secrets:
            if secret in message:
                message = message.replace(secret, "***")
                changed = True
        if changed:
            record.msg = message
            record.args = ()
        return True


def setup_logging(level: str = "INFO", secrets: list[str] | None = None) -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    )
    redactor = RedactingFilter(secrets or [])
    handler.addFilter(redactor)
    root.addHandler(handler)

    for noisy in ("httpx", "httpcore", "aiogram.event", "aiosqlite"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.getLogger(__name__).info("logging configured at %s", level.upper())
