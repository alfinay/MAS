"""Проверка всех трёх паттернов MCP одной командой.

Запуск из папки проекта:  python -m tests.smoke_test
Сетевой сервер тест поднимает сам, если порт 8000 свободен.
Внешний сервер проверяется последним: без интернета этот шаг будет пропущен.
"""

import socket
import subprocess
import sys
import time

import anyio

from mcp_clients import remote_client, sse_client, stdio_client
from mcp_clients.base import ToolError
from mcp_clients.sanitizer import sanitize

passed = 0


def ok(text: str) -> None:
    global passed
    passed += 1
    print(f"  ✔ {text}")


def port_open() -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", 8000)) == 0


async def check_stdio() -> None:
    print("1. stdio")
    tools, packets = await stdio_client.list_tools()
    names = {t.name for t in tools}
    assert {"list_calculators", "bmi", "findrisc", "ckd_epi_2021", "mifflin_st_jeor"} <= names
    assert packets[0]["json"]["method"] == "initialize"
    ok(f"initialize → tools/list: {len(tools)} инструментов")

    out = await stdio_client.call_tool("bmi", {"weight_kg": 70, "height_cm": 175})
    assert out.structured["value"] == 22.86 and out.structured["risk"] == "green"
    ok("ИМТ 70 кг / 175 см = 22.86, зелёный")

    out = await stdio_client.call_tool("ckd_epi_2021", {"creatinine": 1.0, "age": 50, "sex": "male",
                                                        "creatinine_unit": "mg/dL"})
    assert 85 < out.structured["value"] < 95  # справочное значение ≈ 91
    ok(f"CKD-EPI 2021: креатинин 1,0 мг/дл, 50 лет, муж. → {out.structured['value']}")

    try:
        await stdio_client.call_tool("bmi", {"weight_kg": 5, "height_cm": 170})
        raise AssertionError("ожидалась ошибка")
    except ToolError as exc:
        ok(f"граница Pydantic → ToolError: {exc.message[:60]}…")

    try:
        await stdio_client.call_tool("ckd_epi_2021", {"creatinine": 1500, "age": 50, "sex": "male",
                                                      "creatinine_unit": "mg/dL"})
        raise AssertionError("ожидалась ошибка")
    except ToolError as exc:
        assert "вне допустимых границ" in exc.message
        ok("ValueError сервера → понятная ToolError")


async def check_sse() -> None:
    print("2. SSE, порт 8000")
    tools, _ = await sse_client.list_tools()
    assert len([t for t in tools if t.name != "list_calculators"]) >= 5
    schemas = {t.name: t.input_schema for t in tools}
    ok(f"{len(tools)} инструментов по нозологиям")

    out = await sse_client.call_tool("glasgow_coma_scale", {"eye": 2, "verbal": 2, "motor": 4},
                                     schemas["glasgow_coma_scale"])
    assert out.structured["value"] == 8 and out.structured["risk"] == "red"
    ok("ШКГ 2+2+4 = 8, красный")

    out = await sse_client.call_tool("cha2ds2_vasc", {"age": "78", "sex": "ж", "hypertension": "да"},
                                     schemas["cha2ds2_vasc"])
    assert out.structured["value"] == 4 and out.notes
    ok(f"санитайзер: «78», «ж», «да» → CHA₂DS₂-VASc = 4; заметок: {len(out.notes)}")

    out = await sse_client.call_tool("qsofa", {"respiratory_rate": 90, "systolic_bp": 120},
                                     schemas["qsofa"])
    assert out.structured["warnings"]
    ok("ЧДД 90 → предупреждение, а не падение")


def check_sanitizer() -> None:
    print("3. санитайзер (без сети)")
    schema = {"properties": {"w": {"type": "number"}, "eye": {"type": "integer"},
                             "flag": {"type": "boolean"}, "sex": {"enum": ["male", "female"]}}}
    clean, notes = sanitize({"w": "72,5", "flag": "нет", "sex": "М"}, schema)
    assert clean == {"w": 72.5, "eye": 4, "flag": False, "sex": "male"}, clean
    ok("«72,5» → 72.5, «нет» → False, «М» → male, eye по умолчанию 4")


async def check_remote() -> None:
    print("4. внешний PubMed MCP")
    try:
        tools, _ = await remote_client.list_tools()
        ok(f"подключено, показываем инструментов: {len(tools)}")
        out = await remote_client.call_tool("pubmed_search_articles", {"query": "qSOFA sepsis", "maxResults": 2})
        assert "PMID" in out.text
        ok("поиск статей в PubMed")
        t = time.time()
        again = await remote_client.call_tool("pubmed_search_articles", {"query": "qSOFA sepsis", "maxResults": 2})
        assert again.from_cache and time.time() - t < 0.5
        ok("повторный запрос из кэша")
    except remote_client.RemoteTimeout as exc:
        print(f"  ⚠ пропущено: {exc}")


def main() -> None:
    server = None
    if not port_open():
        server = subprocess.Popen([sys.executable, "-m", "clinical_net.server"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(30):
            if port_open():
                break
            time.sleep(0.3)
    try:
        anyio.run(check_stdio)
        anyio.run(check_sse)
        check_sanitizer()
        anyio.run(check_remote)
    finally:
        if server:
            server.terminate()
    print(f"\nВсе проверки пройдены: {passed}")


if __name__ == "__main__":
    main()
