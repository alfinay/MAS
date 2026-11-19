# Практическое задание 2. MCP в клиническом веб-приложении

Три паттерна подключения инструментов по протоколу Model Context Protocol в одном
интерфейсе Streamlit:

| # | Паттерн | Сервер | Транспорт | Инструменты |
|---|---|---|---|---|
| 1 | Изолированный локальный | `clinical_stdio` — свой, дочерний процесс | stdio (stdin/stdout) | ИМТ, FINDRISC, СКФ CKD-EPI 2021, Миффлин — Сан Жеор + `list_calculators` |
| 2 | Локальный сетевой микросервис | `clinical_net` — свой, отдельный процесс | SSE, `http://127.0.0.1:8000/sse` | CHA₂DS₂-VASc, САД (MAP), Кокрофт — Голт, шкала Глазго, qSOFA, HOMA-IR + `list_calculators` |
| 3 | Внешний распределённый | [pubmed-mcp-server](https://glama.ai/mcp/servers/cyanheads/pubmed-mcp-server) из каталога Glama.ai | Streamable HTTP, `https://pubmed.caseyjhand.com/mcp` | поиск статей, загрузка статей, MeSH, проверка написания |

Тема калькуляторов stdio-сервера — профилактика и питание; сетевой сервер разбит по
нозологиям: кардиология, нефрология, реанимация, эндокринология.

## Запуск

Нужен Python 3.11 или 3.12.

```bash
python -m venv .venv && .venv\Scripts\pip install -r requirements.txt && .venv\Scripts\streamlit run app.py
```

(macOS / Linux: `.venv/bin/pip` и `.venv/bin/streamlit`.)

Сетевой сервер можно запустить кнопкой «Запустить сетевой сервер» в боковой панели
или отдельно:

```bash
python -m clinical_net.server
```

stdio-сервер отдельно запускать не нужно: клиент сам стартует его командой
`python -m clinical_stdio.server` при каждом обращении.

Проверка всех трёх паттернов:

```bash
python -m tests.smoke_test
```

## Архитектура

```
app.py (Streamlit)  ── селектор источника, поиск, форма по JSON-схеме, карточка, инспектор
      │
mcp_clients/        ── слой MCP-клиентов
  ├─ stdio_client   ── StdioServerParameters + stdio_client, журнал сервера в logs/
  ├─ sse_client     ── mcp.client.sse, санитайзер, 3 повторные попытки (0,5 / 1 / 2 с)
  ├─ remote_client  ── streamable_http_client, таймаут 15 с, кэш ответов на 1 час
  ├─ sanitizer      ── приведение типов («72,5» → 72.5, «да» → True) и Smart Defaults
  ├─ inspector      ── перехват потоков сессии: сырые JSON-RPC запросы и ответы
  └─ base           ── initialize → tools/list | tools/call → закрытие; isError → ToolError
      │
clinical_stdio/server.py   clinical_net/server.py   https://pubmed.caseyjhand.com/mcp
```

Все вызовы идут через протокол MCP: интерфейс не импортирует функции калькуляторов.

### Обработка ошибок

- **stdio:** границы параметров заданы в Pydantic (`Field(ge=…, le=…)`). Логическая ошибка
  внутри калькулятора (`ValueError`) превращается в `ToolError` с понятным текстом; клиент
  получает `isError: true` и поднимает свою `ToolError`, интерфейс показывает её как сообщение.
- **SSE:** значения за физиологическими границами не роняют сервер — расчёт выполняется,
  в ответ добавляется предупреждение. Невозможные значения (ноль, отрицательные) — понятная ошибка.
- **Внешний сервер:** спиннер, таймаут 15 секунд, кэш одинаковых запросов на час.
- Любое неожиданное исключение перехватывается в `app.py`: приложение не падает.

### Журналирование

В stdio-сервере журнал пишется только в `stderr`: `stdout` занят протоколом JSON-RPC, и любой
лишний `print()` сломал бы обмен. Клиент перенаправляет `stderr` сервера в `logs/stdio_server.log`.

## Подключение к IDE

`.cursor/mcp.json` подключает все три сервера в Cursor. Откройте в Cursor папку
`Практика_2_MCP`, чтобы команда `python -m clinical_stdio.server` нашла пакет; для `clinical-net`
сетевой сервер должен быть запущен.

## Источники методик

ВОЗ TRS 894 (ИМТ) · Lindström 2003, DOI 10.2337/diacare.26.3.725 (FINDRISC) ·
Inker 2021, DOI 10.1056/NEJMoa2102953 (CKD-EPI 2021) · Mifflin 1990, DOI 10.1093/ajcn/51.2.241 ·
Lip 2010, DOI 10.1378/chest.09-1584 (CHA₂DS₂-VASc) · Cockcroft 1976, DOI 10.1159/000180580 ·
Teasdale 1974, DOI 10.1016/S0140-6736(74)91639-0 (GCS) · Seymour 2016, DOI 10.1001/jama.2016.0288 (qSOFA) ·
Matthews 1985, DOI 10.1007/BF00280883 (HOMA-IR) · Surviving Sepsis Campaign 2021 (MAP).

Расчёты справочные и не заменяют решение врача.
