"""Общая часть клиентов: сессия MCP поверх любого транспорта.

Жизненный цикл одного обращения: initialize → tools/list или tools/call →
закрытие каналов. Ошибку инструмента (isError) превращаем в ToolError с
понятным пользователю текстом.
"""

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from mcp.client.session import ClientSession

from .inspector import PacketLog, TapRead, TapWrite


class ToolError(Exception):
    """Ошибка инструмента в виде, понятном пользователю."""

    def __init__(self, tool: str, message: str):
        self.tool = tool
        self.message = message
        super().__init__(f"{tool}: {message}")


@dataclass
class ToolInfo:
    name: str
    title: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class CallOutcome:
    """Результат вызова: данные, текст и журнал пакетов для инспектора."""

    structured: dict[str, Any] | None
    text: str
    packets: list[dict[str, Any]] = field(default_factory=list)


@asynccontextmanager
async def tapped_session(transport_cm, log: PacketLog):
    """Открыть транспорт, обернуть потоки инспектором и пройти initialize."""
    async with transport_cm as streams:
        read, write = streams[0], streams[1]
        async with ClientSession(TapRead(read, log), TapWrite(write, log)) as session:
            await session.initialize()
            yield session


async def list_tools_on(session: ClientSession) -> list[ToolInfo]:
    result = await session.list_tools()
    return [
        ToolInfo(
            name=t.name,
            title=t.title or t.name,
            description=t.description or "",
            input_schema=t.input_schema or {},
        )
        for t in result.tools
    ]


async def call_tool_on(session: ClientSession, name: str, arguments: dict[str, Any]) -> tuple[dict | None, str, bool]:
    result = await session.call_tool(name, arguments)
    text = "\n".join(getattr(c, "text", "") for c in result.content if getattr(c, "text", None))
    return result.structured_content, text, bool(result.is_error)


def finish_call(name: str, structured, text: str, is_error: bool, packets: list[dict]) -> CallOutcome:
    """Превратить ответ в CallOutcome или в понятную ToolError.

    ToolError бросаем уже после закрытия сессии: внутри неё группы задач SDK
    заворачивают исключение в ExceptionGroup.
    """
    if is_error:
        err = ToolError(name, humanize_error(text))
        err.packets = packets
        raise err
    return CallOutcome(structured, text, packets)


def humanize_error(text: str) -> str:
    """Сделать сообщение сервера короче и понятнее."""
    if "validation error" in text.lower():
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        hints = [ln.split(" [type=")[0] for ln in lines if ln.startswith("Input should") or ln.startswith("Field required")]
        fields = [ln for ln in lines if ln and " " not in ln]
        msg = "Параметры не прошли проверку"
        if fields:
            msg += f" ({', '.join(fields)})"
        if hints:
            msg += ": " + "; ".join(hints)
        return msg
    return text.replace("Error executing tool", "Ошибка инструмента").strip()
