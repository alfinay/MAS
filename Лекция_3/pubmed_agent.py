# pubmed_agent.py
"""Агент-источник: PubMed (NCBI).

Ищет свежие медицинские публикации по темам через открытый API NCBI
E-utilities (https://www.ncbi.nlm.nih.gov/books/NBK25501/). Поиск идёт в
два шага: ``esearch`` возвращает список PMID, ``efetch`` – полные записи
в формате XML. Разбор выполняется стандартной библиотекой.

Без ключа NCBI разрешает 3 запроса в секунду, с ключом – 10. Ключ можно
положить в переменную окружения ``NCBI_API_KEY``.

Возвращает единый формат записи, общий для всех источников:
    source, identifier, title, url, abstract, authors, published, journal, doi
"""

import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import date
from typing import Dict, List, Optional

import requests

from utils import resolve_date_range, token_is_exact, topic_tokens

logger = logging.getLogger("archiv_tracker.pubmed")

SOURCE_ID = "pubmed"
SOURCE_NAME = "pubmed.ncbi.nlm.nih.gov"
EUTILS_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
PUBMED_URL = "https://pubmed.ncbi.nlm.nih.gov"
# NCBI просит указывать имя программы в запросах
TOOL_NAME = "mas_publication_tracker"

_MONTHS = {
    m: i for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"],
        start=1,
    )
}


def _clean(text: str) -> str:
    """Убрать переносы строк и лишние пробелы."""
    return re.sub(r"\s+", " ", text or "").strip()


def _text(elem: Optional[ET.Element]) -> str:
    """Весь текст элемента вместе с вложенными тегами (<i>, <sup> …)."""
    if elem is None:
        return ""
    return _clean("".join(elem.itertext()))


def _token_clause(token: str) -> str:
    """Одно слово темы в синтаксисе PubMed, поиск по заголовку и аннотации.

    Аббревиатуры ищутся целиком. Остальные слова – с усечением
    (``agent*`` находит ``agents`` и ``agentic``), как у агента arXiv.
    PubMed разрешает усечение только для слов от четырёх символов.
    """
    safe = re.sub(r"[^A-Za-z0-9]", "", token)
    if not safe:
        return ""
    if token_is_exact(token) or len(safe) < 4:
        return f'"{safe}"[tiab]'
    return f"{safe.lower()}*[tiab]"


def _word_clause(word: str) -> str:
    """Слово темы; составное слово через дефис ищется фразой.

    В медицинских текстах части по отдельности слишком частые:
    ``multi*`` находит «multiple», ``agent*`` – лекарственные «agents».
    Поэтому «multi-agent» превращается в фразу ``"multi-agent*"``
    (PubMed находит и «multi agent») плюс слитное написание ``multiagent*``.
    """
    parts = topic_tokens(word)
    if len(parts) < 2:
        return _token_clause(word)
    safe = [re.sub(r"[^A-Za-z0-9]", "", p) for p in parts]
    phrase = "-".join(p for p in safe if p)
    joined = "".join(safe).lower()
    return f'("{phrase}*"[tiab] OR {joined}*[tiab])'


def build_query(topics: List[str]) -> str:
    """Темы через OR, слова одной темы через AND, без фиксированного порядка."""
    clauses = []
    for topic in topics:
        parts = [c for c in (_word_clause(w) for w in topic.split()) if c]
        if parts:
            clauses.append("(" + " AND ".join(parts) + ")")
    return " OR ".join(clauses)


_last_request_at = 0.0


def _api_get(endpoint: str, params: Dict) -> requests.Response:
    """Запрос к E-utilities с паузой между вызовами (лимит NCBI)."""
    global _last_request_at
    api_key = os.environ.get("NCBI_API_KEY")
    pause = 0.11 if api_key else 0.34
    wait = pause - (time.time() - _last_request_at)
    if _last_request_at and wait > 0:
        time.sleep(wait)
    params = dict(params, db="pubmed", tool=TOOL_NAME)
    if api_key:
        params["api_key"] = api_key
    resp = requests.get(f"{EUTILS_URL}/{endpoint}", params=params, timeout=30)
    _last_request_at = time.time()
    resp.raise_for_status()
    return resp


def _search_ids(query: str, rows: int, date_from: date, date_to: date) -> List[str]:
    """PMID свежих записей, новые первыми.

    ``datetype=edat`` – дата появления записи в PubMed (Entrez date).
    Границы ``mindate``/``maxdate`` включительные. Параметр ``sort`` не
    передаём: по умолчанию PubMed сортирует по той же дате, новые первыми,
    а ``sort=pub_date`` упорядочил бы по дате выпуска журнала.
    """
    resp = _api_get(
        "esearch.fcgi",
        {
            "term": query,
            "retmode": "json",
            "retmax": rows,
            "datetype": "edat",
            "mindate": date_from.strftime("%Y/%m/%d"),
            "maxdate": date_to.strftime("%Y/%m/%d"),
        },
    )
    result = resp.json().get("esearchresult", {})
    if "ERROR" in result:
        raise RuntimeError(result["ERROR"])
    ids = result.get("idlist", [])
    logger.info(f"📥 PubMed: всего совпадений {result.get('count', '?')}, загружено {len(ids)}")
    return ids


def _history_date(article: ET.Element) -> str:
    """Дата появления в PubMed (``PubStatus="pubmed"``) в виде ГГГГ-ММ-ДД."""
    for status in ("pubmed", "entrez"):
        node = article.find(f"PubmedData/History/PubMedPubDate[@PubStatus='{status}']")
        if node is not None:
            try:
                return date(
                    int(node.findtext("Year")),
                    int(node.findtext("Month")),
                    int(node.findtext("Day")),
                ).isoformat()
            except (TypeError, ValueError):
                continue
    return ""


def _journal_date(citation: ET.Element) -> str:
    """Дата выпуска журнала: год, иногда месяц."""
    pub = citation.find("Article/Journal/JournalIssue/PubDate")
    if pub is None:
        return ""
    year = pub.findtext("Year") or (pub.findtext("MedlineDate") or "")[:4]
    month = _MONTHS.get((pub.findtext("Month") or "")[:3].lower())
    return f"{year}-{month:02d}" if year and month else year


def _authors(citation: ET.Element) -> str:
    names = []
    for author in citation.findall("Article/AuthorList/Author"):
        collective = author.findtext("CollectiveName")
        if collective:
            names.append(_clean(collective))
            continue
        last = author.findtext("LastName") or ""
        initials = author.findtext("Initials") or ""
        if last:
            names.append(_clean(f"{last} {initials}"))
    return ", ".join(names)


def _abstract(citation: ET.Element) -> str:
    """Склеить разделы структурированной аннотации: «METHODS: …»."""
    parts = []
    for node in citation.findall("Article/Abstract/AbstractText"):
        text = _text(node)
        if not text:
            continue
        label = node.get("Label")
        parts.append(f"{label}: {text}" if label else text)
    return " ".join(parts)


def _parse_articles(root: ET.Element) -> List[Dict]:
    items = []
    for article in root.findall("PubmedArticle"):
        citation = article.find("MedlineCitation")
        if citation is None:
            continue
        pmid = citation.findtext("PMID")
        if not pmid:
            continue
        doi = article.findtext("PubmedData/ArticleIdList/ArticleId[@IdType='doi']") or ""
        items.append(
            {
                "source": SOURCE_ID,
                "identifier": f"pubmed:{pmid}",
                "title": _text(citation.find("Article/ArticleTitle")) or "(без названия)",
                "url": f"{PUBMED_URL}/{pmid}/",
                "abstract": _abstract(citation),
                "authors": _authors(citation),
                "published": _history_date(article) or _journal_date(citation),
                "journal": _clean(citation.findtext("Article/Journal/Title") or ""),
                "doi": doi,
            }
        )
    return items


def fetch(
    topics: List[str],
    rows: int = 20,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> List[Dict]:
    """Получить публикации PubMed по темам и диапазону дат."""
    date_from, date_to = resolve_date_range(date_from=date_from, date_to=date_to)
    query = build_query(topics)
    if not query:
        logger.warning("Темы для PubMed пустые – поиск не выполняется.")
        return []
    logger.info(
        f"🌐 Запрос к PubMed: {query}; даты {date_from.isoformat()} — "
        f"{date_to.isoformat()} (до {rows} записей)"
    )
    ids = _search_ids(query, rows, date_from, date_to)
    if not ids:
        return []
    resp = _api_get("efetch.fcgi", {"id": ",".join(ids), "retmode": "xml"})
    by_id = {it["identifier"]: it for it in _parse_articles(ET.fromstring(resp.content))}
    # efetch не гарантирует порядок – возвращаем в порядке esearch
    items = [by_id[f"pubmed:{pmid}"] for pmid in ids if f"pubmed:{pmid}" in by_id]
    no_abstract = sum(1 for it in items if not it["abstract"])
    logger.info(f"📥 PubMed: отдано {len(items)}, без аннотации {no_abstract}")
    return items
