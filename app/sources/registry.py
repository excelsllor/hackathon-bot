from __future__ import annotations

from app.sources.codeforces import CodeforcesAdapter
from app.sources.codenrock import CodenrockAdapter
from app.sources.devfolio import DevfolioAdapter
from app.sources.devpost import DevpostAdapter
from app.sources.fsp_calendar import FspCalendarAdapter
from app.sources.habr import HabrRssAdapter
from app.sources.mlh import MlhAdapter
from app.sources.ods import OdsAdapter
from app.sources.rss_generic import GenericRssAdapter
from app.sources.telegram_public import TelegramPublicAdapter

_ADAPTERS = {
    "devpost_api": DevpostAdapter(),
    "devfolio_html": DevfolioAdapter(),
    "ods_html": OdsAdapter(),
    "habr_rss": HabrRssAdapter(),
    "telegram_public": TelegramPublicAdapter(),
    "mlh_html": MlhAdapter(),
    "codeforces_api": CodeforcesAdapter(),
    "codenrock_api": CodenrockAdapter(),
    "rss_generic": GenericRssAdapter(),
    "fsp_calendar": FspCalendarAdapter(),
}


def get_adapter(kind: str):
    try:
        return _ADAPTERS[kind]
    except KeyError as exc:
        raise ValueError(f"unsupported source kind: {kind}") from exc


def supported_kinds() -> list[str]:
    return sorted(_ADAPTERS)
