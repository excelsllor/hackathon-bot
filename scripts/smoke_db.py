#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.db import Database
from app.logging_setup import setup_logging
from app.models import User
from app.pipeline.classify import load_topics
from app.pipeline.eligibility import load_eligibility
from app.pipeline.service import Pipeline
from app.services.alerts import AlertManager
from app.services.search import search_items
from app.services.users import get_or_create_user
from app.sources.base import load_sources
from app.sources.http import HttpClient


async def main() -> None:
    settings = get_settings()
    settings.data_dir = Path(__file__).resolve().parents[1] / "data"
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.db_path = str(settings.data_dir / "smoke.db")
    db_file = Path(settings.db_path)
    if db_file.exists():
        db_file.unlink()
    setup_logging("INFO", settings.secrets)

    topics = load_topics(settings.topics_yaml)
    eligibility = load_eligibility(settings.eligibility_yaml)
    sources = load_sources(settings.sources_yaml)
    for source in sources:
        if source.kind == "devpost_api":
            source.options["pages"] = 3

    db = Database(settings.database_path)
    await db.init()
    http = HttpClient(settings.user_agent, settings.http_timeout_seconds, settings.http_max_bytes)
    pipeline = Pipeline(
        settings, db, sources, topics, eligibility, http, AlertManager(settings)
    )
    results = await pipeline.refresh_due_sources(force=True)
    for result in results:
        print(result)

    async with db.session() as session:
        user = await get_or_create_user(
            session, platform="tg", platform_user_id=1, settings=settings
        )
        user_id = user.id
    async with db.session() as session:
        user = await session.get(User, user_id)
        groups = await search_items(session, user, topics.order, topics, limit=200)
    print("\nGROUPS:")
    for topic_id, items in groups:
        print(f"{topic_id}: {len(items)}")
        for item in items[:5]:
            print(
                " -",
                item.title[:90],
                "|",
                item.reg_deadline or item.ends_at or item.starts_at,
                "|",
                item.format,
                "|",
                item.geo,
                "|",
                item.eligibility,
                "|",
                item.url,
            )
    await http.close()
    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
