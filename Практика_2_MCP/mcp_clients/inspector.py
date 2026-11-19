"""Инспектор JSON-RPC: перехват сообщений между клиентом и сервером.

SDK отдаёт клиентской сессии пару потоков (чтение, запись). Мы оборачиваем
их «прослушкой»: каждое сообщение пишется в журнал как сырой JSON-RPC и
передаётся дальше без изменений.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import anyio


@dataclass
class PacketLog:
    """Журнал пакетов одного обращения к серверу."""

    packets: list[dict[str, Any]] = field(default_factory=list)

    def add(self, direction: str, message: Any) -> None:
        try:
            raw = message.model_dump(mode="json", by_alias=True, exclude_none=True)
        except AttributeError:
            raw = {"repr": repr(message)}
        self.packets.append(
            {"direction": direction, "time": datetime.now().strftime("%H:%M:%S.%f")[:-3], "json": raw}
        )


class TapRead:
    """Поток чтения (сервер → клиент) с записью в журнал."""

    def __init__(self, inner, log: PacketLog):
        self._inner = inner
        self._log = log

    async def receive(self):
        item = await self._inner.receive()
        if not isinstance(item, Exception):
            self._log.add("← сервер", item.message)
        return item

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return await self.receive()
        except (anyio.EndOfStream, anyio.ClosedResourceError):
            raise StopAsyncIteration

    async def aclose(self):
        await self._inner.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.aclose()

    def __getattr__(self, name):
        return getattr(self._inner, name)


class TapWrite:
    """Поток записи (клиент → сервер) с записью в журнал."""

    def __init__(self, inner, log: PacketLog):
        self._inner = inner
        self._log = log

    async def send(self, item):
        self._log.add("→ клиент", item.message)
        await self._inner.send(item)

    async def aclose(self):
        await self._inner.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.aclose()

    def __getattr__(self, name):
        return getattr(self._inner, name)
