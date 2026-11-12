# medrxiv_agent.py
"""Агент-источник: medRxiv (препринты по медицине).

Работает через открытый API https://api.biorxiv.org/ (общий для bioRxiv и
medRxiv). API не умеет искать по словам: он отдаёт все препринты за период
страницами по 30 штук. Поэтому агент загружает весь период и сам отбирает
препринты, в заголовке или аннотации которых есть тема.

Препринт — работа до рецензирования. Агент помечает это в поле ``note``,
а если работа уже вышла в журнале, указывает DOI журнальной статьи.

Возвращает единый формат записи, общий для всех источников:
    source, identifier, title, url, abstract, authors, published,
    journal, doi, note
"""

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

import requests

from utils import resolve_date_range, token_is_exact, topic_tokens

logger = logging.getLogger("archiv_tracker.medrxiv")

SOURCE_ID = "medrxiv"
SOURCE_NAME = "medrxiv.org"
API_URL = "https://api.biorxiv.org/details/medrxiv"
# Страницы грузятся параллельно: один запрос идёт 3–13 секунд
WORKERS = 6


@lru_cache(maxsize=512)
def _load_page(date_from: str, date_to: str, cursor: int) -> Tuple[int, Tuple[Dict, ...]]:
    """Одна страница API: (всего записей за период, записи страницы).

    Результат кэшируется: повторный поиск за тот же период в панели
    не загружает страницы заново.
    """
    resp = requests.get(f"{API_URL}/{date_from}/{date_to}/{cursor}/json", timeout=60)
    resp.raise_for_status()
    data = resp.json()
    messages = data.get("messages") or [{}]
    total = int(messages[0].get("total") or 0)
    return total, tuple(data.get("collection") or [])


def _load_period(date_from: date, date_to: date) -> List[Dict]:
    """Все препринты за период, все версии."""
    start, end = date_from.isoformat(), date_to.isoformat()
    total, first = _load_page(start, end, 0)
    if not first:
        return []
    page = len(first)
    cursors = list(range(page, total, page))
    logger.info(f"📥 medRxiv: за период {total} записей, страниц {len(cursors) + 1}")
    records = list(first)
    with ThreadPoolExecutor(WORKERS) as pool:
        for _, batch in pool.map(lambda c: _load_page(start, end, c), cursors):
            records.extend(batch)
    return records


def _word_pattern(word: str) -> str:
    """Регулярное выражение для одного слова темы.

    Правила те же, что у агента PubMed: аббревиатура и короткое слово —
    целиком, длинное слово — как префикс, слово через дефис — фразой
    (``multi-agent`` находит «multi-agent», «multi agent» и «multiagent»,
    но не «multiple … agents»).
    """
    parts = [re.escape(p) for p in topic_tokens(word)]
    if len(parts) > 1:
        return r"(?<!\w)" + r"[\s\-]?".join(parts) + r"\w*"
    token = word.strip()
    if token_is_exact(token) or len(re.sub(r"[^A-Za-z0-9]", "", token)) < 4:
        return r"(?<!\w)" + re.escape(token) + r"(?!\w)"
    return r"(?<!\w)" + re.escape(token) + r"\w*"


def matches_topic(text: str, topics: List[str]) -> Optional[str]:
    """Первая тема, все слова которой есть в тексте, или ``None``."""
    for topic in topics:
        words = topic.split()
        if words and all(
            re.search(_word_pattern(w), text, flags=re.IGNORECASE) for w in words
        ):
            return topic
    return None


def _to_item(rec: Dict) -> Dict:
    doi = rec.get("doi", "")
    version = rec.get("version") or "1"
    published = rec.get("published") or "NA"
    if published != "NA":
        note = f"Препринт; уже опубликован в журнале, DOI {published}"
    else:
        note = "Препринт: не прошёл рецензирование"
    return {
        "source": SOURCE_ID,
        "identifier": f"medrxiv:{doi}",
        "title": re.sub(r"\s+", " ", rec.get("title") or "").strip() or "(без названия)",
        "url": f"https://www.medrxiv.org/content/{doi}v{version}",
        "abstract": re.sub(r"\s+", " ", rec.get("abstract") or "").strip(),
        "authors": (rec.get("authors") or "").replace(";", ","),
        "published": rec.get("date", ""),
        "journal": f"medRxiv · {rec.get('category', '')}".rstrip(" ·"),
        "doi": doi,
        "note": note,
    }


def fetch(
    topics: List[str],
    rows: int = 20,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> List[Dict]:
    """Получить препринты medRxiv по темам и диапазону дат."""
    date_from, date_to = resolve_date_range(date_from=date_from, date_to=date_to)
    if not any(t.strip() for t in topics):
        logger.warning("Темы для medRxiv пустые – поиск не выполняется.")
        return []
    logger.info(
        f"🌐 Запрос к medRxiv: все препринты {date_from.isoformat()} — "
        f"{date_to.isoformat()}, отбор по темам: {', '.join(topics)}"
    )
    # Одна работа встречается несколькими версиями – оставляем последнюю
    latest: Dict[str, Dict] = {}
    for rec in _load_period(date_from, date_to):
        doi = rec.get("doi")
        if doi and int(rec.get("version") or 1) >= int(latest.get(doi, {}).get("version") or 0):
            latest[doi] = rec
    found = [
        _to_item(rec)
        for rec in latest.values()
        if matches_topic(f"{rec.get('title', '')}\n{rec.get('abstract', '')}", topics)
    ]
    found.sort(key=lambda it: it["published"], reverse=True)
    logger.info(
        f"📥 medRxiv: уникальных работ {len(latest)}, по темам {len(found)}, "
        f"отдано {min(len(found), rows)}"
    )
    return found[:rows]
