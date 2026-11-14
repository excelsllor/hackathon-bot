#!/usr/bin/env python3
"""Force-refresh sources from the command line.

Usage:
  DATA_DIR=/var/lib/hackathon-bot DB_PATH=/var/lib/hackathon-bot/bot.db \
    /opt/hackathon-bot/venv/bin/python scripts/refresh.py codenrock devpost
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.db import Database
from app.logging_setup import setup_logging
from app.pipeline.classify import load_topics
from app.pipeline.eligibility import load_eligibility
from app.pipeline.service import Pipeline
from app.services.alerts import AlertManager
from app.sources.base import load_sources
from app.sources.http import HttpClient


async def main(codes: list[str]) -> int:
    settings = get_settings()
    setup_logging(settings.log_level, settings.secrets)
    topics = load_topics(settings.topics_yaml)
    eligibility = load_eligibility(settings.eligibility_yaml)
    sources = load_sources(settings.sources_yaml)
    db = Database(settings.database_path)
    await db.init()
    http = HttpClient(
        settings.user_agent, settings.http_timeout_seconds, settings.http_max_bytes
    )
    pipeline = Pipeline(
        settings, db, sources, topics, eligibility, http, AlertManager(settings)
    )
    try:
        results = await pipeline.refresh_due_sources(codes=codes or None, force=True)
        for result in results:
            print(result)
        return 0 if all(r.get("status") in {"ok", "fresh", "skipped"} for r in results) else 1
    finally:
        await http.close()
        await db.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
