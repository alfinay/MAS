# scraper.py
"""Агент-координатор поиска.

Опрашивает подключённые агенты-источники (archive.org, arXiv.org, PubMed …),
отбрасывает уже просмотренные записи, передаёт остальные валидатору
и возвращает список новых релевантных записей.

Чтобы добавить новый источник, создайте модуль с константами SOURCE_ID,
SOURCE_NAME и функцией fetch(topics, rows) и зарегистрируйте его в SOURCES.
"""

import logging
from datetime import date
from typing import Dict, List, Optional

import archive_agent
import arxiv_agent
import pubmed_agent
from utils import load_config, get_history, update_history, resolve_date_range
from validator import validate_item  # валидатор релевантности

logger = logging.getLogger("archiv_tracker.scraper")

# Реестр доступных агентов-источников: id → модуль
SOURCES = {
    archive_agent.SOURCE_ID: archive_agent,
    arxiv_agent.SOURCE_ID: arxiv_agent,
    pubmed_agent.SOURCE_ID: pubmed_agent,
}
# Человекочитаемые названия источников (для интерфейса)
SOURCE_NAMES = {sid: mod.SOURCE_NAME for sid, mod in SOURCES.items()}


def get_new_items(
    topics: Optional[List[str]] = None,
    sources: Optional[List[str]] = None,
    rows: int = 20,
    skip_seen: bool = True,
    save_history: bool = True,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> List[Dict]:
    """Вернуть новые релевантные записи из всех подключённых источников.

    topics       – список тем (по умолчанию из config.yaml);
    sources      – список id источников (по умолчанию из config.yaml);
    rows         – сколько последних записей запрашивать у КАЖДОГО источника;
    skip_seen    – пропускать записи, которые уже есть в истории;
    save_history – записывать найденные записи в history.json;
    date_from, date_to – включительные границы дат (по умолчанию последняя неделя).
    """
    cfg = load_config()
    if topics is None:
        topics = cfg.get("topics", [])
    if sources is None:
        sources = cfg.get("sources", list(SOURCES))
    if not topics:
        logger.warning("Темы не заданы – поиск не выполняется.")
        return []
    if not sources:
        logger.warning("Ни один источник не подключён – поиск не выполняется.")
        return []
    date_from, date_to = resolve_date_range(date_from=date_from, date_to=date_to)
    logger.info(f"📅 Диапазон дат: {date_from.isoformat()} — {date_to.isoformat()}")

    history = set(get_history()) if skip_seen else set()
    if skip_seen:
        logger.info(f"📚 В истории {len(history)} записей – они будут пропущены")

    new_items: List[Dict] = []
    for sid in sources:
        agent = SOURCES.get(sid)
        if agent is None:
            logger.warning(f"Неизвестный источник «{sid}» – пропуск")
            continue
        logger.info(f"━━━━━━━━ Источник: {agent.SOURCE_NAME} ━━━━━━━━")
        try:
            items = agent.fetch(
                topics, rows=rows, date_from=date_from, date_to=date_to,
            )
        except Exception as e:
            logger.error(f"❗ {agent.SOURCE_NAME}: ошибка запроса – {e}")
            continue

        accepted = skipped = rejected = 0
        logger.info(f"🧪 Валидатор проверяет {len(items)} записей на релевантность…")
        for n, it in enumerate(items, start=1):
            if it["identifier"] in history:
                skipped += 1
                logger.info(f"⏭ [{n}/{len(items)}] {it['identifier']}: уже в истории – пропуск")
                continue
            logger.info(f"[{n}/{len(items)}] «{it['title'][:90]}»")
            if validate_item(it, topics):
                new_items.append(it)
                accepted += 1
            else:
                rejected += 1
        logger.info(
            f"📊 {agent.SOURCE_NAME}: принято {accepted}, отклонено {rejected}, "
            f"пропущено (история) {skipped}"
        )

    logger.info(f"📦 Всего новых релевантных записей: {len(new_items)}")
    if new_items and save_history:
        new_ids = [it["identifier"] for it in new_items]
        update_history(new_ids)
        logger.info(f"💾 В историю добавлено {len(new_ids)} записей")
    return new_items


if __name__ == "__main__":
    for item in get_new_items():
        print(item["source"], item["title"], item["url"])
