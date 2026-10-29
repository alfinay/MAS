# validator.py
"""Валидатор найденных публикаций.

Проверяет, что в тексте есть все слова темы. Слова делятся по пробелам и
дефисам, порядок не важен: «medical multi-agent» совпадает с «Multi-Agent
… Medical». Короткие аббревиатуры вроде RAG ищутся только как целое слово.
"""

import logging
import re
from typing import List, Dict, Optional
from utils import extract_text_from_url, token_is_exact, topic_tokens

logger = logging.getLogger("archiv_tracker.validator")

# Источники, у которых аннотация приходит из API: страницу не загружаем
ABSTRACT_SOURCES = {"arxiv", "pubmed"}


def _token_in_text(token: str, text: str) -> bool:
    """Слово темы есть в тексте.

    Аббревиатура должна совпасть целиком. Более длинное слово совпадает
    и как префикс: ``agent`` находит ``agents`` и ``agentic``.
    """
    if token_is_exact(token) or len(re.sub(r"[^A-Za-z0-9]", "", token)) < 4:
        pattern = r"(?<!\w)" + re.escape(token) + r"(?!\w)"
    else:
        pattern = r"(?<!\w)" + re.escape(token) + r"\w*"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def _find_topic(text: str, topics: List[str]) -> Optional[str]:
    """Найти первую тему, все слова которой встречаются в тексте.

    Сравнение без учёта регистра. Аббревиатура «RAG» не срабатывает
    на «storage» или «fragment».
    """
    for topic in topics:
        tokens = topic_tokens(topic)
        if tokens and all(_token_in_text(token, text) for token in tokens):
            return topic
    return None


def validate_item(item: Dict, topics: List[str]) -> bool:
    """Возвращает ``True``, если хотя бы одна тема присутствует в заголовке,
    аннотации или (для archive.org) в тексте страницы.

    Parameters
    ----------
    item: dict
        Должен содержать ключи ``title`` и ``url``; опционально ``abstract``
        и ``source``.
    topics: list[str]
        Список тем (ключевых слов) из конфигурации.

    Returns
    -------
    bool
        ``True`` – релевантно, ``False`` – не релевантно.
    """
    ident = item.get("identifier", "?")

    # 1. Заголовок и аннотация вместе: слова темы могут быть в разных полях.
    head = str(item.get("title", ""))
    abstract = item.get("abstract") or ""
    topic = _find_topic(f"{head}\n{abstract}", topics)
    if topic:
        logger.info(f"✅ {ident}: тема «{topic}» найдена в заголовке или аннотации")
        return True

    # 2. Для arXiv и PubMed заголовка и аннотации достаточно – страницу не загружаем.
    if item.get("source") in ABSTRACT_SOURCES:
        logger.info(f"❌ {ident}: темы нет ни в заголовке, ни в аннотации – отклонено")
        return False

    # 3. Для остальных источников проверяем текст страницы.
    logger.info(f"🔎 {ident}: в заголовке/аннотации тем нет, загружаем страницу…")
    try:
        text = extract_text_from_url(item["url"])
        topic = _find_topic(text, topics)
        if topic:
            logger.info(f"✅ {ident}: тема «{topic}» найдена в тексте страницы")
            return True
    except Exception as e:
        # игнорируем ошибки извлечения текста
        logger.warning(f"⚠️ {ident}: ошибка загрузки страницы ({e}) – отклонено")
        return False

    logger.info(f"❌ {ident}: ни одна тема не найдена – отклонено")
    return False
