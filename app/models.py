from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def json_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    return []


def dump_list(values: list[str] | set[str] | tuple[str, ...] | None) -> str:
    return json.dumps(sorted(set(values or [])), ensure_ascii=False)


class Base(DeclarativeBase):
    pass


class Item(Base):
    __tablename__ = "items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_url: Mapped[str] = mapped_column(String(2048), unique=True, index=True)
    source_code: Mapped[str] = mapped_column(String(64), index=True)
    external_id: Mapped[str | None] = mapped_column(String(160), nullable=True)

    title: Mapped[str] = mapped_column(String(600))
    description: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(String(2048))

    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reg_deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    date_confidence: Mapped[str] = mapped_column(String(16), default="unknown")
    raw_dates: Mapped[str] = mapped_column(Text, default="")

    format: Mapped[str] = mapped_column(String(16), default="unknown")
    city: Mapped[str | None] = mapped_column(String(128), nullable=True)
    country: Mapped[str | None] = mapped_column(String(64), nullable=True)
    geo: Mapped[str] = mapped_column(String(8), default="intl", index=True)
    region: Mapped[str] = mapped_column(String(16), default="russia", index=True)
    language: Mapped[str] = mapped_column(String(8), default="ru")
    eligibility: Mapped[str] = mapped_column(String(24), default="unknown", index=True)

    topics_json: Mapped[str] = mapped_column("topics", Text, default="[]")
    tags_json: Mapped[str] = mapped_column("tags", Text, default="[]")
    matched_json: Mapped[str] = mapped_column("matched_keywords", Text, default="[]")
    extra_sources_json: Mapped[str] = mapped_column("extra_sources", Text, default="[]")

    content_hash: Mapped[str] = mapped_column(String(64), default="")
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    deliveries: Mapped[list[Delivery]] = relationship(
        back_populates="item", cascade="all, delete-orphan"
    )

    @property
    def topics(self) -> list[str]:
        return json_list(self.topics_json)

    @property
    def tags(self) -> list[str]:
        return json_list(self.tags_json)

    @property
    def matched_keywords(self) -> list[str]:
        return json_list(self.matched_json)

    @property
    def extra_sources(self) -> list[str]:
        return json_list(self.extra_sources_json)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("platform", "platform_user_id", name="uq_user_platform"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[str] = mapped_column(String(8), index=True)
    platform_user_id: Mapped[str] = mapped_column(String(64), index=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)

    digest_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    digest_time: Mapped[str] = mapped_column(String(5), default="10:00")
    digest_days: Mapped[int] = mapped_column(Integer, default=1)
    format_pref: Mapped[str] = mapped_column(String(8), default="any")
    scope_pref: Mapped[str] = mapped_column(String(8), default="ru")
    strict_eligibility: Mapped[bool] = mapped_column(Boolean, default=True)

    last_digest_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    last_digest_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    subscriptions: Mapped[list[Subscription]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Subscription(Base):
    __tablename__ = "subscriptions"
    __table_args__ = (UniqueConstraint("user_id", "topic", name="uq_subscription"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    topic: Mapped[str] = mapped_column(String(32), index=True)

    user: Mapped[User] = relationship(back_populates="subscriptions")


class Delivery(Base):
    __tablename__ = "deliveries"
    __table_args__ = (
        UniqueConstraint("user_id", "item_id", "kind", name="uq_delivery"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="digest")
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    item: Mapped[Item] = relationship(back_populates="deliveries")


class HiddenItem(Base):
    __tablename__ = "hidden_items"
    __table_args__ = (UniqueConstraint("user_id", "item_id", name="uq_hidden"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FavoriteItem(Base):
    __tablename__ = "favorite_items"
    __table_args__ = (UniqueConstraint("user_id", "item_id", name="uq_favorite"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SourceState(Base):
    __tablename__ = "source_state"

    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    last_new_count: Mapped[int] = mapped_column(Integer, default=0)


class FetchRun(Base):
    __tablename__ = "fetch_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_code: Mapped[str] = mapped_column(String(64), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(24), default="running")
    items_found: Mapped[int] = mapped_column(Integer, default=0)
    items_new: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
