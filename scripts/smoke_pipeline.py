#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.db import Database
from app.logging_setup import setup_logging
from app.pipeline.classify import classify_text, load_topics
from app.pipeline.eligibility import load_eligibility
from app.pipeline.normalize import canonicalize_url
from app.pipeline.service import Pipeline
from app.services.alerts import AlertManager
from app.sources.base import load_sources
from app.sources.http import HttpClient
from app.sources.registry import get_adapter


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources", default="")
    parser.add_argument("--pages", type=int, default=3)
    args = parser.parse_args()

    settings = get_settings()
    if not os.getenv("DATA_DIR") and not os.getenv("DB_PATH"):
        settings.data_dir = Path(__file__).resolve().parents[1] / "data"
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        settings.db_path = str(settings.data_dir / "smoke.db")
    setup_logging("INFO", settings.secrets)
    topics = load_topics(settings.topics_yaml)
    eligibility = load_eligibility(settings.eligibility_yaml)
    sources = load_sources(settings.sources_yaml)
    selected = [s for s in sources if s.enabled]
    if args.sources:
        wanted = {x.strip() for x in args.sources.split(",") if x.strip()}
        selected = [s for s in sources if s.code in wanted]

    db = Database(settings.database_path)
    await db.init()
    http = HttpClient(settings.user_agent, settings.http_timeout_seconds, settings.http_max_bytes)
    Pipeline(settings, db, sources, topics, eligibility, http, AlertManager(settings))

    for source in selected:
        if source.kind == "devpost_api":
            source.options["pages"] = args.pages
        print(f"=== {source.code} ({source.kind}) ===")
        try:
            adapter = get_adapter(source.kind)
            from datetime import datetime, timezone

            raw_items = await adapter.fetch(source, http, datetime.now(timezone.utc))
            print("raw items:", len(raw_items))
            for raw in raw_items[:3]:
                topic_ids, _, _ = classify_text(
                    raw.title,
                    raw.description,
                    raw.tags,
                    topics,
                    trusted_event=source.kind
                    in {
                        "devpost_api",
                        "devfolio_html",
                        "ods_html",
                        "mlh_html",
                        "codeforces_api",
                        "codenrock_api",
                    },
                    default_topic="ds" if source.kind == "ods_html" else "it",
                )
                print(
                    " -",
                    raw.title[:100],
                    "|",
                    raw.starts_at,
                    "|",
                    raw.reg_deadline,
                    "|",
                    raw.format,
                    "|",
                    raw.geo,
                    "|",
                    topic_ids,
                    "|",
                    canonicalize_url(raw.url),
                )
        except Exception as exc:
            print("ERROR:", type(exc).__name__, exc)
    await http.close()
    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
