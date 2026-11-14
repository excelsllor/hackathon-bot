from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db import Database, naive_utc
from app.models import (
    Delivery,
    FetchRun,
    Item,
    SourceState,
    dump_list,
    ensure_utc,
)
from app.pipeline.classify import TopicsConfig, classify_text, mapped_tags
from app.pipeline.dedupe import find_duplicate
from app.pipeline.eligibility import EligibilityConfig, assess_eligibility
from app.pipeline.normalize import (
    canonicalize_url,
    clean_text,
    content_hash,
    detect_language,
    strip_html,
    truncate,
)
from app.pipeline.regions import classify_region
from app.services.alerts import AlertManager
from app.sources.base import RawItem, SourceConfig, SourceError
from app.sources.http import HttpClient
from app.sources.registry import get_adapter

logger = logging.getLogger(__name__)

TRUSTED_EVENT_KINDS = {
    "devpost_api",
    "devfolio_html",
    "ods_html",
    "mlh_html",
    "codeforces_api",
    "codenrock_api",
}


class Pipeline:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        sources: list[SourceConfig],
        topics: TopicsConfig,
        eligibility: EligibilityConfig,
        http: HttpClient,
        alerts: AlertManager,
    ) -> None:
        self.settings = settings
        self.db = db
        self.sources = sources
        self.topics = topics
        self.eligibility = eligibility
        self.http = http
        self.alerts = alerts
        self._locks: dict[str, asyncio.Lock] = {}
        self._semaphore = asyncio.Semaphore(settings.max_concurrent_fetches)

    async def refresh_due_sources(
        self,
        codes: list[str] | None = None,
        *,
        force: bool = False,
        max_age_minutes: int | None = None,
    ) -> list[dict]:
        selected = [
            source
            for source in self.sources
            if source.enabled and (not codes or source.code in codes)
        ]

        async def run_one(source: SourceConfig) -> dict:
            async with self._semaphore:
                return await self.refresh_source(
                    source, force=force, max_age_minutes=max_age_minutes
                )

        if not selected:
            return []
        return list(await asyncio.gather(*(run_one(source) for source in selected)))

    async def refresh_source(
        self,
        config: SourceConfig,
        *,
        force: bool = False,
        max_age_minutes: int | None = None,
    ) -> dict:
        lock = self._locks.setdefault(config.code, asyncio.Lock())
        if lock.locked():
            return {"source": config.code, "status": "skipped", "reason": "already running"}

        async with lock:
            now = datetime.now(timezone.utc)
            async with self.db.session() as session:
                state = await session.get(SourceState, config.code)
                if state and state.last_run_at and not force:
                    last_run = ensure_utc(state.last_run_at)
                    age_limit = max_age_minutes or config.interval_minutes
                    if last_run and now - last_run < timedelta(minutes=age_limit):
                        return {"source": config.code, "status": "fresh"}
                run = FetchRun(source_code=config.code, started_at=now, status="running")
                session.add(run)
                await session.flush()
                run_id = run.id

            items_found = 0
            new_count = 0
            try:
                adapter = get_adapter(config.kind)
                raw_items = await adapter.fetch(config, self.http, now)
                items_found = len(raw_items)
                async with self.db.session() as session:
                    new_count = await self._upsert_items(session, config, raw_items, now)
                    state = await session.get(SourceState, config.code)
                    if state is None:
                        state = SourceState(code=config.code)
                        session.add(state)
                    state.last_run_at = now
                    state.last_status = "ok"
                    state.last_error = None
                    state.consecutive_failures = 0
                    state.last_new_count = new_count
                    run = await session.get(FetchRun, run_id)
                    if run:
                        run.finished_at = now
                        run.status = "ok"
                        run.items_found = items_found
                        run.items_new = new_count
                logger.info(
                    "source %s: fetched=%s new=%s", config.code, items_found, new_count
                )
                return {
                    "source": config.code,
                    "status": "ok",
                    "found": items_found,
                    "new": new_count,
                }
            except Exception as exc:
                error = _short_error(exc)
                logger.exception("source %s failed: %s", config.code, error)
                async with self.db.session() as session:
                    state = await session.get(SourceState, config.code)
                    if state is None:
                        state = SourceState(code=config.code)
                        session.add(state)
                    state.last_run_at = now
                    state.last_status = "error"
                    state.last_error = error[:2000]
                    state.consecutive_failures = (state.consecutive_failures or 0) + 1
                    run = await session.get(FetchRun, run_id)
                    if run:
                        run.finished_at = now
                        run.status = "error"
                        run.items_found = items_found
                        run.error = error[:2000]
                    failures = state.consecutive_failures
                if failures >= self.settings.admin_alert_after_failures:
                    await self.alerts.notify(
                        f"Источник {config.code} падает {failures} раз подряд: {error}",
                        key=f"source:{config.code}",
                    )
                return {"source": config.code, "status": "error", "error": error}

    async def _upsert_items(
        self,
        session: AsyncSession,
        config: SourceConfig,
        raw_items: list[RawItem],
        now: datetime,
    ) -> int:
        trusted_event = config.kind in TRUSTED_EVENT_KINDS
        default_topic = "ds" if config.kind == "ods_html" else "it"
        new_count = 0

        for raw in raw_items:
            canonical = canonicalize_url(raw.url)
            if not canonical.startswith("http"):
                continue
            if not raw.title.strip():
                continue

            topics, tags, matched = classify_text(
                raw.title,
                raw.description,
                raw.tags,
                self.topics,
                trusted_event=trusted_event,
                default_topic=default_topic,
            )
            mapped = mapped_tags(raw.tags, self.topics)
            if mapped:
                merged = set(topics) | set(mapped)
                topics = [t for t in self.topics.order if t in merged]
                if len(topics) > 1 and "it" in topics:
                    topics = [t for t in topics if t != "it"]
            if not topics:
                continue

            fmt = raw.format
            if config.geo == "intl" and fmt != "online":
                continue
            ends_at = ensure_utc(raw.ends_at)
            if ends_at is not None and ends_at < now - timedelta(days=1):
                continue

            region_text = " ".join(
                [
                    raw.title,
                    raw.description,
                    " ".join(raw.tags),
                    raw.city or "",
                ]
            )
            region = classify_region(
                geo=config.geo,
                source_region=config.region,
                format=fmt,
                text=region_text,
            )
            eligibility_text = region_text
            eligibility = (
                "restricted"
                if raw.eligibility == "restricted"
                else assess_eligibility(
                    eligibility_text,
                    self.eligibility,
                    geo=config.geo,
                    format=fmt,
                    hint=raw.eligibility if raw.eligibility != "unknown" else "",
                )
            )
            if raw.eligibility == "restricted":
                eligibility = "restricted"

            description = truncate(strip_html(raw.description), 1500)
            language = raw.language or detect_language(f"{raw.title} {description}")

            existing = (
                await session.execute(
                    select(Item).where(Item.canonical_url == canonical)
                )
            ).scalar_one_or_none()

            if existing is not None:
                existing.title = clean_text(raw.title, 600) or existing.title
                if description:
                    existing.description = description
                existing.url = canonical
                if raw.starts_at is not None:
                    existing.starts_at = raw.starts_at
                if raw.ends_at is not None:
                    existing.ends_at = raw.ends_at
                if raw.reg_deadline is not None:
                    existing.reg_deadline = raw.reg_deadline
                existing.date_confidence = raw.date_confidence
                existing.raw_dates = raw.raw_dates[:1000]
                existing.format = fmt
                existing.city = raw.city
                existing.country = raw.country
                existing.language = language
                existing.geo = config.geo
                existing.region = region
                if eligibility != "unknown" or existing.eligibility == "unknown":
                    existing.eligibility = eligibility
                existing.topics_json = dump_list(topics)
                existing.tags_json = dump_list(tags)
                existing.matched_json = dump_list(matched)
                existing.content_hash = content_hash(
                    existing.title,
                    canonical,
                    str(raw.starts_at or ""),
                    str(raw.ends_at or ""),
                    str(raw.reg_deadline or ""),
                )
                existing.last_seen_at = now
                existing.is_active = True
                continue

            duplicate = await find_duplicate(
                session,
                title=raw.title,
                starts_at=raw.starts_at,
                source_code=config.code,
            )
            if duplicate is not None:
                merged_sources = set(duplicate.extra_sources) | {config.code}
                duplicate.extra_sources_json = dump_list(
                    [duplicate.source_code, *merged_sources]
                )
                duplicate.last_seen_at = now
                duplicate.is_active = True
                if duplicate.description == "" and description:
                    duplicate.description = description
                if duplicate.reg_deadline is None and raw.reg_deadline is not None:
                    duplicate.reg_deadline = raw.reg_deadline
                continue

            item = Item(
                canonical_url=canonical,
                source_code=config.code,
                external_id=(raw.external_id or "")[:160] or None,
                title=clean_text(raw.title, 600),
                description=description,
                url=canonical,
                starts_at=raw.starts_at,
                ends_at=raw.ends_at,
                reg_deadline=raw.reg_deadline,
                date_confidence=raw.date_confidence,
                raw_dates=raw.raw_dates[:1000],
                format=fmt,
                city=raw.city,
                country=raw.country,
                geo=config.geo,
                region=region,
                language=language,
                eligibility=eligibility,
                topics_json=dump_list(topics),
                tags_json=dump_list(tags),
                matched_json=dump_list(matched),
                extra_sources_json=dump_list([config.code]),
                content_hash=content_hash(
                    raw.title,
                    canonical,
                    str(raw.starts_at or ""),
                    str(raw.ends_at or ""),
                    str(raw.reg_deadline or ""),
                ),
                first_seen_at=now,
                last_seen_at=now,
                is_active=True,
            )
            session.add(item)
            new_count += 1

        await session.execute(
            update(Item)
            .where(
                Item.source_code == config.code,
                Item.last_seen_at < naive_utc(now),
                Item.is_active.is_(True),
            )
            .values(is_active=False)
            .execution_options(synchronize_session=False)
        )
        await session.flush()
        return new_count

    async def cleanup(self) -> dict[str, int]:
        now = datetime.now(timezone.utc)
        cutoff_items = now - timedelta(days=self.settings.cleanup_days)
        async with self.db.session() as session:
            items = (await session.execute(select(Item))).scalars().all()
            deactivated = 0
            for item in items:
                ends = ensure_utc(item.ends_at)
                if ends is not None and ends < cutoff_items and item.is_active:
                    item.is_active = False
                    deactivated += 1
            deleted_runs = await session.execute(
                delete(FetchRun).where(FetchRun.started_at < naive_utc(now - timedelta(days=90)))
            )
            deleted_deliveries = await session.execute(
                delete(Delivery).where(Delivery.sent_at < naive_utc(now - timedelta(days=180)))
            )
        return {
            "deactivated": deactivated,
            "deleted_runs": int(deleted_runs.rowcount or 0),
            "deleted_deliveries": int(deleted_deliveries.rowcount or 0),
        }

    async def state_summary(self) -> list[dict]:
        async with self.db.session() as session:
            states = (await session.execute(select(SourceState))).scalars().all()
        return [
            {
                "code": state.code,
                "last_run_at": state.last_run_at.isoformat() if state.last_run_at else None,
                "status": state.last_status,
                "failures": state.consecutive_failures,
                "last_error": state.last_error,
                "last_new": state.last_new_count,
            }
            for state in states
        ]


def _short_error(exc: Exception) -> str:
    if isinstance(exc, SourceError):
        return str(exc)[:2000]
    return f"{type(exc).__name__}: {exc}"[:2000]
