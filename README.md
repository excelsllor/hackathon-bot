# Hackathon Bot

Telegram/VK-бот, который собирает хакатоны, чемпионаты и челленджи по программированию из API, RSS, HTML и публичных Telegram-каналов.

Бот умеет:

- фильтровать события по темам, формату, региону и срокам;
- скрывать школьные события и недоступные международные хакатоны;
- дедуплицировать одно событие из нескольких источников;
- присылать результаты по кнопке и по расписанию;
- вести личные подписки на темы дайджеста;
- работать с региональными блоками: Липецкая область, Россия, Международные;
- хранить историю источников и ошибок парсинга в SQLite.

## Стек

- Python 3.10+
- aiogram для Telegram
- vkbottle для VK
- SQLAlchemy 2 + SQLite/aiosqlite
- httpx, selectolax, feedparser, dateparser
- APScheduler
- pytest, ruff

## Быстрый запуск

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env
```

Заполните в `.env`:

- `TG_TOKEN` и `TG_ADMIN_IDS` для Telegram;
- `VK_TOKEN`, `VK_GROUP_ID`, `VK_ADMIN_IDS` для VK.

Запуск:

```bash
python -m app.main
```

Проверки:

```bash
python -m pytest -q
ruff check app tests scripts
```

## Структура проекта

```text
app/
  bots/          Telegram и VK-интерфейсы
  pipeline/      классификация, регионы, даты, дедупликация
  services/      поиск, дайджест, пользователи
  sources/       адаптеры источников
config/
  sources.yaml   список источников
  topics.yaml    темы и ключевые слова
  eligibility.yaml правила доступности
scripts/         refresh, backup и служебные скрипты
tests/           unit-тесты
```

## Как работают источники

Каждый источник описывается в `config/sources.yaml` и использует один из адаптеров в `app/sources/`.

Пример записи:

```yaml
sources:
  - code: my_feed
    name: "Мой источник"
    kind: rss_generic
    url: "https://example.com/hackathons/feed.xml"
    enabled: false
    interval_minutes: 180
    priority: 50
    geo: ru
    region: ""
    language: ru
    online_only: false
    options: {}
```

Поля:

| Поле | Назначение |
| --- | --- |
| `code` | Уникальный идентификатор источника: `[a-z0-9_]`, 2–64 символа |
| `name` | Название в интерфейсе и логах |
| `kind` | Тип адаптера из таблицы ниже |
| `url` | HTTP(S)-адрес источника |
| `enabled` | Включён ли источник |
| `interval_minutes` | Как часто обновлять |
| `priority` | Порядок обработки; меньше — раньше |
| `geo` | `ru` или `intl` |
| `region` | `lipetsk` для локального источника, иначе пусто |
| `language` | `ru` или `en` |
| `online_only` | Подсказка для источника |
| `options` | Параметры конкретного адаптера |

### Встроенные адаптеры

| `kind` | Источник | Особенности |
| --- | --- | --- |
| `devpost_api` | Devpost | API, пагинация через `options.pages` |
| `devfolio_html` | Devfolio | HTML-парсинг |
| `codenrock_api` | Codenrock | Типы `hackathon`, `challenge`, `ctf`, `sport_programming`, `ml_ai` |
| `ods_html` | ODS.ai | HTML-парсинг соревнований |
| `telegram_public` | Публичные Telegram-каналы | `https://t.me/s/<channel>`, опция `require_dates` |
| `rss_generic` | Любой RSS/Atom | Опция `require_dates` |
| `habr_rss` | Habr | RSS-хаб хакатонов |
| `fsp_calendar` | Календарь ФСП | Фильтр по Липецкой области |
| `mlh_html` | MLH | Сезонные события |
| `codeforces_api` | Codeforces | Контесты, не хакатоны |

### Добавить источник без кода

Если подходит существующий адаптер, достаточно добавить запись в `config/sources.yaml`:

```yaml
  - code: challenge_feed
    name: "Challenge"
    kind: rss_generic
    url: "https://example.com/challenges/feed.xml"
    enabled: true
    interval_minutes: 240
    priority: 25
    geo: ru
    language: ru
    options:
      require_dates: true
```

Затем проверьте источник:

```bash
python scripts/refresh.py challenge_feed
```

На сервере:

```bash
runuser -u hackbot -- env \
  DATA_DIR=/var/lib/hackathon-bot \
  DB_PATH=/var/lib/hackathon-bot/bot.db \
  /opt/hackathon-bot/venv/bin/python \
  /opt/hackathon-bot/current/scripts/refresh.py challenge_feed
```

### Добавить новый адаптер

1. Создайте файл `app/sources/my_source.py`.
2. Реализуйте класс с методом `fetch`:

```python
from __future__ import annotations

from datetime import datetime

from app.pipeline.normalize import clean_text
from app.sources.base import RawItem, SourceConfig, SourceError
from app.sources.http import HttpClient


class MySourceAdapter:
    kind = "my_source_api"

    async def fetch(
        self,
        config: SourceConfig,
        http: HttpClient,
        now: datetime,
    ) -> list[RawItem]:
        data = await http.get_json(config.url)
        if not isinstance(data, dict):
            raise SourceError(f"{config.code}: unexpected response")

        items: list[RawItem] = []
        for entry in data.get("items", []):
            title = clean_text(entry.get("title"))
            url = str(entry.get("url") or "")
            if not title or not url:
                continue
            items.append(
                RawItem(
                    source_code=config.code,
                    external_id=str(entry.get("id") or ""),
                    title=title,
                    description=entry.get("description") or "",
                    url=url,
                    starts_at=None,
                    ends_at=None,
                    format="online",
                    city=None,
                    geo=config.geo,
                    language="ru",
                    eligibility="unknown",
                    tags=["challenge"],
                )
            )
        return items
```

3. Зарегистрируйте адаптер в `app/sources/registry.py`:

```python
from app.sources.my_source import MySourceAdapter

_ADAPTERS = {
    ...,
    "my_source_api": MySourceAdapter(),
}
```

4. Добавьте `kind` в `_ALLOWED_KINDS` в `app/sources/base.py`.
5. Добавьте запись в `config/sources.yaml`.
6. Напишите тест на парсер в `tests/`.
7. Запустите `pytest` и `ruff`.

### Контракт `RawItem`

Пайплайн ожидает от адаптера:

| Поле | Обязательность | Комментарий |
| --- | --- | --- |
| `source_code` | да | Совпадает с `code` источника |
| `title` | да | Название события |
| `url` | да | Ссылка на событие |
| `description` | нет | Описание; HTML будет очищен |
| `external_id` | желательно | Для стабильной идентификации |
| `starts_at` / `ends_at` | желательно | Timezone-aware datetime |
| `reg_deadline` | желательно | Дедлайн регистрации |
| `format` | да | `online`, `offline`, `hybrid` или `unknown` |
| `geo` | да | `ru` или `intl` |
| `region` | через конфиг | `lipetsk` для локальных источников |
| `city`, `country` | желательно | Для региональной фильтрации |
| `language` | да | `ru` или `en` |
| `eligibility` | желательно | `ru`, `worldwide`, `online_open`, `check`, `restricted` |
| `tags` | нет | Темы и теги события |

### Правила качества

- Не добавляйте в адаптер токены, cookies и приватные URL.
- Не фильтруйте события по темам и регионам внутри адаптера: это делает пайплайн.
- Приводите даты к timezone-aware UTC; отображение переводится в МСК.
- Для одного события используйте стабильный `url` и `external_id`.
- Не создавайте отдельные адаптеры для каждого сайта, если подходит `rss_generic`.
- Если источник отдаёт не-события (статьи, митапы, вакансии), фильтруйте их по типу или названию.
- После добавления проверьте не только `found`, но и `new`, регион, формат и отсутствие дублей.

## Digest и поиск

Поиск и дайджест используют общий `search_items`: одинаковые фильтры, дедупликация и лимит. Поэтому количество событий в дайджесте совпадает с ручным поиском для тех же тем и настроек.

## Безопасность

- `.env`, `.secrets/`, `data/`, базы и виртуальные окружения не коммитятся.
- Не храните реальные токены в `sources.yaml` или README.
- Для серверных секретов используйте `/etc/hackathon-bot/env` с правами `600`.
