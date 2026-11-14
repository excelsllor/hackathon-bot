from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from app.pipeline.normalize import normalize_for_match

HACKATHON_CUE = re.compile(
    r"(?i)(?:хакатон\w*|hackathon\w*|\bhack\b|\bjam\b|джем\w*|чемпионат\w*|"
    r"соревнован\w*|конкурс\w*|\bcontest\w*|\bchallenge\w*|hack\s*day|"
    r"hackweek|хакатон-)"
)

PROGRAMMING_CUE = re.compile(
    r"(?i)(?:программирован\w*|разработ\w*|\bкод\b|кодинг\w*|\bит\b|\bit\b|айти|"
    r"цифров\w*|технолог\w*|\btech\b|software|developer|engineering|data|"
    r"алгоритм\w*|нейросет\w*|искусственн\w* интеллект|machine learning|"
    r"artificial intelligence|ml\b|ai\b|web\b|mobile\b|android\b|ios\b|"
    r"devops\b|cloud\b|security\b|blockchain\b|game\w*|iot\b|robot\w*|"
    r"python\b|java\b|javascript\b|kotlin\b|swift\b|golang\b|rust\b|"
    r"backend\b|frontend\b|fullstack\b|api\b|database\b|sql\b|"
    r"open source|open data|qa\b|тестирован\w*|тестировщик\w*)"
)


@dataclass(slots=True)
class Topic:
    id: str
    title: str
    emoji: str
    patterns: list[re.Pattern[str]]
    negative: list[re.Pattern[str]]


@dataclass(slots=True)
class TopicsConfig:
    topics: list[Topic]
    tag_map: dict[str, str]

    @property
    def order(self) -> list[str]:
        return [t.id for t in self.topics]

    @property
    def by_id(self) -> dict[str, Topic]:
        return {t.id: t for t in self.topics}

    def title(self, topic_id: str) -> str:
        topic = self.by_id.get(topic_id)
        return topic.title if topic else topic_id

    def emoji(self, topic_id: str) -> str:
        topic = self.by_id.get(topic_id)
        return topic.emoji if topic else "•"

    def display(self, topic_id: str) -> str:
        topic = self.by_id.get(topic_id)
        if not topic:
            return topic_id
        return f"{topic.emoji} {topic.title}"


def load_topics(path: str | Path) -> TopicsConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    entries = raw.get("topics")
    if not isinstance(entries, list) or not entries:
        raise ValueError("topics.yaml: no topics configured")
    topics: list[Topic] = []
    seen: set[str] = set()
    for entry in entries:
        topic_id = str(entry.get("id", "")).strip()
        if not re.fullmatch(r"[a-z0-9_]{2,32}", topic_id):
            raise ValueError(f"invalid topic id: {topic_id!r}")
        if topic_id in seen:
            raise ValueError(f"duplicate topic id: {topic_id}")
        seen.add(topic_id)
        words = []
        for key in ("keywords_ru", "keywords_en"):
            values = entry.get(key) or []
            if isinstance(values, str):
                values = [values]
            words.extend(str(v) for v in values)
        negative = entry.get("negative") or []
        if isinstance(negative, str):
            negative = [negative]
        topics.append(
            Topic(
                id=topic_id,
                title=str(entry.get("title", topic_id)),
                emoji=str(entry.get("emoji", "•")),
                patterns=[_compile_keyword(w) for w in words if w.strip()],
                negative=[_compile_keyword(w) for w in negative if w.strip()],
            )
        )
    tag_map_raw = raw.get("tag_map") or {}
    tag_map = {
        str(k).lower().strip(): str(v)
        for k, v in tag_map_raw.items()
        if isinstance(v, str)
    }
    return TopicsConfig(topics=topics, tag_map=tag_map)


def classify_text(
    title: str,
    description: str,
    tags: list[str] | None,
    config: TopicsConfig,
    *,
    trusted_event: bool,
    default_topic: str = "it",
) -> tuple[list[str], list[str], list[str]]:
    title_n = normalize_for_match(title)
    desc_n = normalize_for_match(description)
    tags_list = [normalize_for_match(t) for t in (tags or []) if t]
    tags_n = " | ".join(tags_list)

    if not trusted_event and not HACKATHON_CUE.search(f"{title_n} {desc_n}"):
        return [], [], []

    scores: dict[str, int] = {}
    matched: list[str] = []
    for topic in config.topics:
        if any(p.search(f"{title_n} {desc_n} {tags_n}") for p in topic.negative):
            continue
        score = 0
        for pattern in topic.patterns:
            if pattern.search(title_n):
                score += 4
                matched.append(pattern.pattern)
            elif pattern.search(tags_n):
                score += 3
                matched.append(pattern.pattern)
            elif pattern.search(desc_n):
                score += 1
                matched.append(pattern.pattern)
        if score >= 3:
            scores[topic.id] = score

    selected = [t.id for t in config.topics if t.id in scores]
    if not selected and trusted_event and default_topic:
        selected = [default_topic]

    if len(selected) > 1 and "it" in selected:
        selected = [t for t in selected if t != "it"]
    if trusted_event and selected == ["it"] and default_topic and default_topic != "it":
        selected = [default_topic]

    if (
        not trusted_event
        and selected == ["it"]
        and not PROGRAMMING_CUE.search(f"{title_n} {desc_n} {tags_n}")
    ):
        return [], _clean_tags(tags or []), _dedupe(matched)[:40]

    return selected, _clean_tags(tags or []), _dedupe(matched)[:40]


def mapped_tags(tags: list[str], config: TopicsConfig) -> list[str]:
    result: list[str] = []
    for tag in tags:
        key = normalize_for_match(tag)
        mapped = config.tag_map.get(key)
        if mapped:
            result.append(mapped)
    return _dedupe(result)


def _compile_keyword(keyword: str) -> re.Pattern[str]:
    normalized = normalize_for_match(keyword)
    parts: list[str] = []
    for char in normalized:
        if char == "*":
            parts.append(r"\w*")
        else:
            parts.append(re.escape(char))
    body = "".join(parts)
    return re.compile(
        rf"(?<![0-9a-zа-я]){body}(?![0-9a-zа-я])",
        re.IGNORECASE | re.UNICODE,
    )


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _clean_tags(tags: list[str]) -> list[str]:
    return _dedupe([str(t).strip() for t in tags if str(t).strip()])[:30]
