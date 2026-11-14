from __future__ import annotations

import re

LIPETSK_RE = re.compile(
    r"(?i)(липецк|липецк(?:ая|ой|ую|е|ом)|елец|елецк|грязи|данков|"
    r"лебедян|усман|чаплыгин|задонск|добринк|доброе|измалков|"
    r"краснинск|тербун|волов|хлевн|долгоруков|становлянск|"
    r"липецкой области|липецкую область)"
)

OTHER_REGION_RE = re.compile(
    r"(?i)(москв|санкт-петербург|петербург|ленинградск|казан|"
    r"новосибирск|екатеринбург|нижн(?:ий|его) новгород|самар|"
    r"омск|челябинск|ростов|уфа|красноярск|воронеж|перм|"
    r"волгоград|краснодар|саратов|тюмен|тольятти|ижевск|"
    r"барнаул|ульяновск|иркутск|хабаровск|ярослав|владивосток|"
    r"махачкал|томск|оренбург|кемеров|новокузнецк|рязан|"
    r"астрахан|пенз|киров|чебоксар|калининград|тула|курск|"
    r"сочи|ставропол|белгород|сургут|владимир|архангельск|"
    r"смоленск|калуга|курган|орёл|орел|череповец|вологда|"
    r"саранск|якутск|магадан|мурманск|тамбов|брянск|псков|"
    r"великий новгород|севастопол|симферопол|майкоп|элист|"
    r"нальчик|владикавказ|грозн|магас|кызыл|абакан|горно-алтайск|"
    r"благовещенск|биробиджан|южно-сахалинск|петропавловск|"
    r"анадыр|нарьян-мар|салехард|ханты-мансийск|"
    r"московской области|московскую область)"
)

REGION_ORDER = ("lipetsk", "russia", "international")
REGION_LABELS = {
    "lipetsk": "📍 Липецкая область",
    "russia": "🇷🇺 Россия",
    "international": "🌍 Международные",
    "other_region": "Другие регионы",
}


def classify_region(
    *, geo: str, source_region: str, format: str, text: str
) -> str:
    if geo == "intl":
        return "international"
    if source_region == "lipetsk" or LIPETSK_RE.search(text):
        return "lipetsk"
    if format == "online":
        return "russia"
    if OTHER_REGION_RE.search(text):
        return "other_region"
    return "russia"


def effective_region(region: str | None, geo: str) -> str:
    if geo == "intl":
        return "international"
    return region or "russia"


def region_allowed(region: str, scope: str) -> bool:
    if region == "other_region":
        return False
    if scope == "ru":
        return region in {"lipetsk", "russia"}
    if scope == "intl":
        return region == "international"
    return True
