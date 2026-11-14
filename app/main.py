from __future__ import annotations

import asyncio
import logging
import signal
import sys

from app.bots.telegram_bot import TelegramBot
from app.bots.vk_bot import VkBot
from app.config import get_settings
from app.db import Database
from app.logging_setup import setup_logging
from app.pipeline.classify import load_topics
from app.pipeline.eligibility import load_eligibility
from app.pipeline.service import Pipeline
from app.scheduler import BotScheduler
from app.services.alerts import AlertManager
from app.sources.base import load_sources
from app.sources.http import HttpClient

logger = logging.getLogger(__name__)


async def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level, settings.secrets)

    topics = load_topics(settings.topics_yaml)
    sources = load_sources(settings.sources_yaml)
    eligibility = load_eligibility(settings.eligibility_yaml)

    db = Database(settings.database_path)
    await db.init()

    http = HttpClient(
        user_agent=settings.user_agent,
        timeout_seconds=settings.http_timeout_seconds,
        max_bytes=settings.http_max_bytes,
    )
    alerts = AlertManager(settings)
    pipeline = Pipeline(settings, db, sources, topics, eligibility, http, alerts)

    tg_bot: TelegramBot | None = None
    vk_bot: VkBot | None = None
    if not settings.parser_only:
        if settings.tg_token:
            tg_bot = TelegramBot(settings, db, pipeline, topics)
        else:
            logger.warning("TG_TOKEN is empty: Telegram bot disabled")
        if settings.vk_token:
            vk_bot = VkBot(settings, db, pipeline, topics)
        else:
            logger.warning("VK_TOKEN is empty: VK bot disabled")

    senders = {}
    if tg_bot:
        senders["tg"] = tg_bot.send_text
    if vk_bot:
        senders["vk"] = vk_bot.send_text

    scheduler = BotScheduler(settings, db, pipeline, topics, senders)
    scheduler.start()

    tasks: list[asyncio.Task] = []
    if tg_bot:
        tasks.append(asyncio.create_task(tg_bot.start(), name="telegram"))
    if vk_bot:
        tasks.append(asyncio.create_task(vk_bot.start(), name="vk"))
    if not tasks:
        logger.warning("no bot transports started; running parser-only")

    stop = asyncio.Event()

    def request_stop(*_args: object) -> None:
        stop.set()

    try:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, request_stop)
            except (NotImplementedError, RuntimeError):
                signal.signal(sig, request_stop)
    except Exception:
        logger.debug("signal handlers could not be installed", exc_info=True)

    try:
        await stop.wait()
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("shutting down")
        scheduler.shutdown()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if tg_bot:
            await tg_bot.close()
        if vk_bot:
            await vk_bot.close()
        await http.close()
        await db.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
