from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=os.getenv("ENV_FILE", str(BASE_DIR / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # bots
    tg_token: str = ""
    tg_admin_ids: str = ""
    vk_token: str = ""
    vk_group_id: int = 0
    vk_admin_ids: str = ""

    # storage / runtime
    data_dir: Path = BASE_DIR / "data"
    db_path: str = ""
    config_dir: Path = BASE_DIR / "config"
    heartbeat_path: str = ""
    parser_only: bool = False

    # behaviour
    timezone: str = "Europe/Moscow"
    log_level: str = "INFO"
    user_agent: str = "HackathonBot/1.0 (+https://t.me/; contact: admin@example.com)"
    on_demand_ttl_minutes: int = 45
    digest_default_time: str = "10:00"
    refresh_tick_minutes: int = 10
    digest_tick_minutes: int = 5
    cleanup_days: int = 30
    wait_for_refresh_seconds: int = 15
    user_rate_limit_seconds: int = 5

    # http
    http_timeout_seconds: float = 20.0
    http_max_bytes: int = 8_000_000
    max_concurrent_fetches: int = 3
    devpost_pages_default: int = 8

    # alerts
    admin_alert_after_failures: int = 3
    admin_alert_cooldown_minutes: int = 60

    # limits
    max_items_per_search: int = 120
    page_size: int = 5
    all_results_limit: int = 300
    all_results_page_size: int = 50
    all_results_max_messages: int = 25
    vk_all_results_max_messages: int = 12
    vk_all_results_delay_seconds: float = 0.65
    message_max_chars: int = 3500
    description_max_chars: int = 320

    # safety
    allow_private_source_ips: bool = False
    log_secrets: bool = False

    extra_admin_ids: list[int] = Field(default_factory=list)

    @property
    def database_path(self) -> Path:
        if self.db_path:
            return Path(self.db_path)
        return self.data_dir / "bot.db"

    @property
    def heartbeat_file(self) -> Path:
        if self.heartbeat_path:
            return Path(self.heartbeat_path)
        return self.data_dir / "heartbeat"

    @property
    def sources_yaml(self) -> Path:
        return self.config_dir / "sources.yaml"

    @property
    def topics_yaml(self) -> Path:
        return self.config_dir / "topics.yaml"

    @property
    def eligibility_yaml(self) -> Path:
        return self.config_dir / "eligibility.yaml"

    def _parse_ids(self, raw: str) -> set[int]:
        result: set[int] = set()
        for part in raw.replace(";", ",").replace(" ", ",").split(","):
            part = part.strip()
            if part.isdigit():
                result.add(int(part))
        return result

    @property
    def tg_admins(self) -> set[int]:
        return self._parse_ids(self.tg_admin_ids) | set(self.extra_admin_ids)

    @property
    def vk_admins(self) -> set[int]:
        return self._parse_ids(self.vk_admin_ids) | set(self.extra_admin_ids)

    @property
    def secrets(self) -> list[str]:
        return [s for s in (self.tg_token, self.vk_token) if s]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings
