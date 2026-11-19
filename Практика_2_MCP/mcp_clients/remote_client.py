"""Клиент 3: внешний MCP-сервер доказательной медицины.

Сервер: pubmed-mcp-server (каталог Glama.ai, github.com/cyanheads/pubmed-mcp-server),
публичный адрес https://pubmed.caseyjhand.com/mcp, транспорт Streamable HTTP.

- Таймаут на всё обращение — 15 секунд, чтобы интерфейс не зависал.
- Кэш ответов на час: одинаковый запрос не тратит лимит NCBI (3 запроса/с).
"""

import json
import time
from typing import Any

import anyio

from mcp.client.streamable_http import streamable_http_client

from .base import CallOutcome, ToolInfo, call_tool_on, finish_call, list_tools_on, tapped_session
from .inspector import PacketLog

URL = "https://pubmed.caseyjhand.com/mcp"
TIMEOUT_S = 15
CACHE_TTL_S = 3600

# Инструменты, которые показываем в интерфейсе (у сервера их 12).
SHOWN_TOOLS = (
    "pubmed_search_articles",
    "pubmed_fetch_articles",
    "pubmed_lookup_mesh",
    "pubmed_spell_check",
)

_cache: dict[str, tuple[float, Any]] = {}


class RemoteTimeout(Exception):
    """Внешний сервер не ответил за отведённое время."""


def _cached(key: str):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL_S:
        return hit[1]
    return None


async def _run(action):
    try:
        with anyio.fail_after(TIMEOUT_S):
            return await action()
    except TimeoutError as exc:
        raise RemoteTimeout(f"Внешний сервер не ответил за {TIMEOUT_S} секунд. Попробуйте позже.") from exc


async def list_tools() -> tuple[list[ToolInfo], list[dict]]:
    hit = _cached("tools/list")
    if hit:
        return hit, []
    log = PacketLog()

    async def action():
        async with tapped_session(streamable_http_client(URL), log) as session:
            return await list_tools_on(session)

    tools = [t for t in await _run(action) if t.name in SHOWN_TOOLS]
    _cache["tools/list"] = (time.time(), tools)
    return tools, log.packets


async def call_tool(name: str, arguments: dict[str, Any]) -> CallOutcome:
    key = "call:" + name + json.dumps(arguments, sort_keys=True, ensure_ascii=False)
    hit = _cached(key)
    if hit:
        outcome = CallOutcome(hit.structured, hit.text, hit.packets)
        outcome.from_cache = True
        return outcome
    log = PacketLog()

    async def action():
        async with tapped_session(streamable_http_client(URL), log) as session:
            return await call_tool_on(session, name, arguments)

    structured, text, is_error = await _run(action)
    outcome = finish_call(name, structured, text, is_error, log.packets)
    _cache[key] = (time.time(), outcome)
    outcome.from_cache = False
    return outcome
