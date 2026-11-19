"""Клиент 1: stdio. Запускает сервер дочерним процессом и говорит с ним
через stdin/stdout. Журнал сервера (stderr) пишется в logs/stdio_server.log.
"""

import sys
from pathlib import Path
from typing import Any

from mcp.client.stdio import StdioServerParameters, stdio_client

from .base import CallOutcome, ToolInfo, call_tool_on, finish_call, list_tools_on, tapped_session
from .inspector import PacketLog

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "logs"

SERVER = StdioServerParameters(
    command=sys.executable,
    args=["-m", "clinical_stdio.server"],
    cwd=str(ROOT),
)


def _errlog():
    LOG_DIR.mkdir(exist_ok=True)
    return open(LOG_DIR / "stdio_server.log", "a", encoding="utf-8")


async def list_tools() -> tuple[list[ToolInfo], list[dict]]:
    log = PacketLog()
    with _errlog() as err:
        async with tapped_session(stdio_client(SERVER, errlog=err), log) as session:
            tools = await list_tools_on(session)
    return tools, log.packets


async def call_tool(name: str, arguments: dict[str, Any]) -> CallOutcome:
    log = PacketLog()
    with _errlog() as err:
        async with tapped_session(stdio_client(SERVER, errlog=err), log) as session:
            structured, text, is_error = await call_tool_on(session, name, arguments)
    return finish_call(name, structured, text, is_error, log.packets)
