from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models import Base


def naive_utc(value: datetime | None) -> datetime | None:
    """SQLite stores naive datetimes; keep comparisons in naive UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_async_engine(
            f"sqlite+aiosqlite:///{self.path}",
            echo=False,
            poolclass=NullPool,
        )
        event.listen(self.engine.sync_engine, "connect", _set_sqlite_pragma)
        self.sessionmaker = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def init(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.run_sync(_migrate_schema)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        session = self.sessionmaker()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    async def close(self) -> None:
        await self.engine.dispose()

    def check_sync(self) -> bool:
        return self.path.exists()


def _migrate_schema(sync_connection) -> None:  # type: ignore[no-untyped-def]
    """Small SQLite-only additive migrations for existing installations."""
    user_columns = {
        row[1]
        for row in sync_connection.exec_driver_sql("PRAGMA table_info(users)").fetchall()
    }
    if "digest_days" not in user_columns:
        sync_connection.exec_driver_sql(
            "ALTER TABLE users ADD COLUMN digest_days INTEGER DEFAULT 127"
        )

    item_columns = {
        row[1]
        for row in sync_connection.exec_driver_sql("PRAGMA table_info(items)").fetchall()
    }
    if "region" not in item_columns:
        sync_connection.exec_driver_sql(
            "ALTER TABLE items ADD COLUMN region VARCHAR(16) DEFAULT 'russia'"
        )
        sync_connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_items_region ON items(region)"
        )
        sync_connection.exec_driver_sql(
            "UPDATE items SET region='international' WHERE geo='intl'"
        )
        sync_connection.exec_driver_sql(
            "UPDATE items SET region='lipetsk' "
            "WHERE geo='ru' AND (title LIKE '%ипецк%' OR description LIKE '%ипецк%' "
            "OR city LIKE '%ипецк%')"
        )

    version = sync_connection.exec_driver_sql("PRAGMA user_version").fetchone()[0]
    if version < 1:
        # Old default was daily digest and "Russia + world" scope.
        sync_connection.exec_driver_sql(
            "UPDATE users SET digest_days=1 WHERE digest_days=127"
        )
        sync_connection.exec_driver_sql(
            "UPDATE users SET scope_pref='ru' WHERE scope_pref='both'"
        )
        sync_connection.exec_driver_sql("PRAGMA user_version=1")

    if version < 2:
        # Strict eligibility is now mandatory and has no UI toggle.
        sync_connection.exec_driver_sql(
            "UPDATE users SET strict_eligibility=1 WHERE strict_eligibility IS NOT 1"
        )
        sync_connection.exec_driver_sql("PRAGMA user_version=2")


def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()
