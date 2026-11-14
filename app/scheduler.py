from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import Settings
from app.db import Database
from app.pipeline.classify import TopicsConfig
from app.pipeline.service import Pipeline
from app.services.digest import Sender, run_digest_tick

logger = logging.getLogger(__name__)


class BotScheduler:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        pipeline: Pipeline,
        topics: TopicsConfig,
        senders: dict[str, Sender],
    ) -> None:
        self.settings = settings
        self.db = db
        self.pipeline = pipeline
        self.topics = topics
        self.senders = senders
        self.scheduler = AsyncIOScheduler(timezone=ZoneInfo(settings.timezone))
        self._initial_task: asyncio.Task | None = None

    def start(self) -> None:
        self.scheduler.add_job(
            self.pipeline.refresh_due_sources,
            IntervalTrigger(minutes=self.settings.refresh_tick_minutes),
            id="refresh",
            coalesce=True,
            max_instances=1,
            misfire_grace_time=300,
        )
        self.scheduler.add_job(
            self._digest_job,
            IntervalTrigger(minutes=self.settings.digest_tick_minutes),
            id="digest",
            coalesce=True,
            max_instances=1,
            misfire_grace_time=300,
        )
        self.scheduler.add_job(
            self._heartbeat,
            IntervalTrigger(minutes=1),
            id="heartbeat",
            coalesce=True,
            max_instances=1,
        )
        self.scheduler.add_job(
            self.pipeline.cleanup,
            CronTrigger(hour=4, minute=0, timezone=ZoneInfo("Europe/Moscow")),
            id="cleanup",
            coalesce=True,
            max_instances=1,
            misfire_grace_time=3600,
        )
        self.scheduler.start()
        self._heartbeat()
        self._initial_task = asyncio.create_task(
            self.pipeline.refresh_due_sources(), name="initial-refresh"
        )
        logger.info("scheduler started")

    async def _digest_job(self) -> None:
        await run_digest_tick(self.db, self.settings, self.topics, self.senders)

    def _heartbeat(self) -> None:
        path = self.settings.heartbeat_file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")

    def shutdown(self) -> None:
        if self._initial_task and not self._initial_task.done():
            self._initial_task.cancel()
        self.scheduler.shutdown(wait=False)
