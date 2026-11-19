"""Клиент 2: локальный сетевой сервер по SSE (http://127.0.0.1:8000/sse).

Асинхронное подключение через mcp.client.sse. Если порт временно недоступен
(сервер перезапускается), клиент делает до трёх повторных попыток с
нарастающей паузой: 0,5 → 1 → 2 секунды.
"""

from typing import Any

import anyio

from mcp.client.sse import sse_client

from .base import CallOutcome, ToolInfo, call_tool_on, finish_call, list_tools_on, tapped_session
from .inspector import PacketLog
from .sanitizer import sanitize

URL = "http://127.0.0.1:8000/sse"
RETRY_DELAYS = (0.5, 1.0, 2.0)


class ServerUnavailable(Exception):
    """Сетевой сервер не ответил после всех попыток."""


def _is_connection_error(exc: BaseException) -> bool:
    """Есть ли среди вложенных исключений ошибка соединения."""
    if isinstance(exc, BaseExceptionGroup):
        return any(_is_connection_error(e) for e in exc.exceptions)
    name = type(exc).__name__
    return isinstance(exc, (ConnectionError, OSError)) or "Connect" in name


async def _with_retries(action):
    last: BaseException | None = None
    for attempt, delay in enumerate((0.0, *RETRY_DELAYS), start=1):
        if delay:
            await anyio.sleep(delay)
        try:
            return await action()
        except Exception as exc:  # noqa: BLE001 — разбираем ниже
            if not _is_connection_error(exc):
                raise
            last = exc
    raise ServerUnavailable(
        f"Сервер {URL} недоступен после {len(RETRY_DELAYS) + 1} попыток. "
        "Запустите его: python -m clinical_net.server"
    ) from last


async def list_tools() -> tuple[list[ToolInfo], list[dict]]:
    log = PacketLog()

    async def action():
        log.packets.clear()
        async with tapped_session(sse_client(URL, timeout=5), log) as session:
            return await list_tools_on(session)

    tools = await _with_retries(action)
    return tools, log.packets


async def call_tool(name: str, arguments: dict[str, Any], schema: dict[str, Any]) -> CallOutcome:
    clean, notes = sanitize(arguments, schema)
    log = PacketLog()

    async def action():
        log.packets.clear()
        async with tapped_session(sse_client(URL, timeout=5), log) as session:
            return await call_tool_on(session, name, clean)

    structured, text, is_error = await _with_retries(action)
    outcome = finish_call(name, structured, text, is_error, log.packets)
    outcome.notes = notes
    return outcome
