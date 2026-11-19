"""Веб-интерфейс: три источника MCP-инструментов в одном окне.

Запуск:  streamlit run app.py

Каждый вызов идёт по протоколу MCP через слой клиентов (mcp_clients),
калькуляторы не импортируются напрямую. Внизу — инспектор пакетов JSON-RPC.
"""

import json
import socket
import subprocess
import sys
from pathlib import Path

import anyio
import pandas as pd
import streamlit as st

from mcp_clients import remote_client, sse_client, stdio_client
from mcp_clients.base import ToolError
from mcp_clients.remote_client import RemoteTimeout
from mcp_clients.sse_client import ServerUnavailable

ROOT = Path(__file__).resolve().parent

SOURCES = {
    "stdio": "Локальный stdio",
    "sse": "Локальный сетевой (порт 8000)",
    "remote": "Внешний сервис доказательной медицины",
}
SOURCE_NOTES = {
    "stdio": "Сервер запускается дочерним процессом, обмен через stdin/stdout. Калькуляторы профилактики.",
    "sse": "Отдельный сервер на http://127.0.0.1:8000/sse. Калькуляторы по нозологиям, санитайзер и повторные попытки.",
    "remote": "pubmed-mcp-server из каталога Glama.ai, https://pubmed.caseyjhand.com/mcp. Таймаут 15 с, кэш 1 ч.",
}
RISK_STYLE = {
    "green": ("#E3F4E8", "#1E7B3C", "Низкий риск / норма"),
    "yellow": ("#FFF4D6", "#8A6100", "Умеренный риск"),
    "red": ("#FDE2E1", "#B42318", "Высокий риск"),
    "info": ("#EAF0FB", "#2B4C8C", "Справочно"),
}
ENUM_LABELS = {
    "male": "мужской", "female": "женский",
    "none": "нет", "second_degree": "дальние родственники", "first_degree": "близкие родственники",
    "sedentary": "сидячий", "light": "лёгкая", "moderate": "умеренная", "high": "высокая", "extreme": "очень высокая",
    "umol/L": "мкмоль/л", "mg/dL": "мг/дл",
}

st.set_page_config(page_title="MCP: клинические калькуляторы", page_icon="🩺", layout="wide")


# ---------- вызовы клиентов (всё через MCP) ----------

def run(fn, *args):
    return anyio.run(fn, *args)


def port_open(host: str = "127.0.0.1", port: int = 8000) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex((host, port)) == 0


def start_sse_server() -> None:
    (ROOT / "logs").mkdir(exist_ok=True)
    log = open(ROOT / "logs" / "sse_server.log", "a", encoding="utf-8")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen([sys.executable, "-m", "clinical_net.server"], cwd=ROOT,
                     stdout=log, stderr=log, creationflags=flags)


def load_tools(source: str):
    """Список инструментов источника, запоминается в сессии."""
    key = f"tools_{source}"
    if key not in st.session_state:
        client = {"stdio": stdio_client, "sse": sse_client, "remote": remote_client}[source]
        tools, packets = run(client.list_tools)
        st.session_state[key] = tools
        st.session_state[f"packets_{source}"] = packets
        st.session_state[f"packets_title_{source}"] = f"tools/list — {SOURCES[source]}"
    return st.session_state[key]


def call(source: str, tool, args: dict):
    if source == "stdio":
        return run(stdio_client.call_tool, tool.name, args)
    if source == "sse":
        return run(sse_client.call_tool, tool.name, args, tool.input_schema)
    return run(remote_client.call_tool, tool.name, args)


def show_error(title: str, text: str) -> None:
    st.error(f"**{title}**\n\n{text}")


# ---------- боковая панель: выбор источника ----------

st.sidebar.title("🩺 MCP-источник")
source = st.sidebar.radio("Источник инструментов", list(SOURCES), format_func=SOURCES.get)
st.sidebar.caption(SOURCE_NOTES[source])

if source == "sse":
    if port_open():
        st.sidebar.success("Сервер на порту 8000 работает")
    else:
        st.sidebar.warning("Сервер на порту 8000 не запущен")
        if st.sidebar.button("Запустить сетевой сервер"):
            start_sse_server()
            with st.spinner("Запускаю сервер…"):
                for _ in range(20):
                    if port_open():
                        break
                    anyio.run(anyio.sleep, 0.3)
            st.session_state.pop("tools_sse", None)
            st.rerun()

if st.sidebar.button("Обновить список инструментов"):
    st.session_state.pop(f"tools_{source}", None)

st.sidebar.divider()
st.sidebar.caption("Расчёты справочные и не заменяют решение врача.")

# ---------- загрузка инструментов ----------

st.title("Клинические калькуляторы через MCP")
st.caption("Три паттерна интеграции: stdio · локальный SSE · внешний сервер")

if source == "sse" and not port_open():
    st.info("Сетевой сервер не запущен. Нажмите «Запустить сетевой сервер» слева "
            "или выполните в терминале: `python -m clinical_net.server`")
    st.stop()

try:
    with st.spinner("Получаю список инструментов (tools/list)…"):
        tools = load_tools(source)
except ServerUnavailable as exc:
    show_error("Сетевой сервер недоступен", str(exc))
    st.stop()
except RemoteTimeout as exc:
    show_error("Внешний сервер не ответил", str(exc))
    st.stop()
except Exception as exc:  # noqa: BLE001 — интерфейс не должен падать
    show_error("Не удалось подключиться к серверу", f"{type(exc).__name__}: {exc}")
    st.stop()

# ---------- поиск и выбор инструмента ----------

# У каждого источника своя строка поиска: слово от прошлого источника не мешает.
query = st.text_input("🔎 Поиск инструмента", placeholder="например: почки, GCS, диабет, BMI, PubMed",
                      key=f"search_{source}")
q = query.strip().lower()
found = [t for t in tools if not q or q in f"{t.name} {t.title} {t.description}".lower()]
# Справочный list_calculators — в конец списка, первым показываем настоящий калькулятор.
found.sort(key=lambda t: t.name == "list_calculators")
st.caption(f"Инструментов у сервера: {len(tools)}, подходит под поиск: {len(found)}")
if not found:
    st.info("Ничего не найдено. Попробуйте другое слово — русское или латинское.")
    st.stop()

tool = st.selectbox(
    "Инструмент", found, format_func=lambda t: f"{t.title}  ·  {t.name}",
    help="В списке можно печатать — он сам подсказывает варианты",
)
st.markdown(f"_{tool.description.splitlines()[0] if tool.description else ''}_")

# ---------- генератор формы по JSON-схеме ----------

schema = tool.input_schema or {}
props: dict = schema.get("properties", {})
required = set(schema.get("required", []))
# Параметры внешнего сервера, которые показываем в форме, с русскими подписями.
REMOTE_LABELS = {
    "query": "Запрос (лучше по-английски, например: diabetes prevention)",
    "maxResults": "Сколько результатов",
    "summaryCount": "Краткие описания для первых N статей",
    "sort": "Сортировка",
    "hasAbstract": "Только с аннотацией",
    "freeFullText": "Только с бесплатным полным текстом",
    "pmids": "PMID статей",
    "includeDetails": "Подробности по термину",
}
REMOTE_DEFAULTS = {"maxResults": 5, "summaryCount": 5}

text_mode = False
if source == "sse" and props:
    text_mode = st.toggle("Ввод текстом (проверить санитайзер: «72,5», «да», «ж»)")


def field_widget(name: str, spec: dict, key: str):
    help_text = spec.get("description")
    label = REMOTE_LABELS.get(name) if source == "remote" else None
    label = label or help_text or spec.get("title") or name
    if len(label) > 70:
        label = spec.get("title") or name
    label = f"{label}{' *' if name in required else ''}"
    default = REMOTE_DEFAULTS.get(name, spec.get("default")) if source == "remote" else spec.get("default")
    kind = spec.get("type")
    if kind is None and "anyOf" in spec:  # необязательные поля: anyOf [тип, null]
        kinds = [x.get("type") for x in spec["anyOf"] if x.get("type") != "null"]
        kind = kinds[0] if kinds else None

    if text_mode and kind in ("number", "integer", "boolean") or text_mode and "enum" in spec:
        return st.text_input(label, value="" if default is None else str(default), key=key, help=help_text)
    if "enum" in spec:
        options = spec["enum"]
        index = options.index(default) if default in options else 0
        return st.selectbox(label, options, index=index, key=key, help=help_text,
                            format_func=lambda v: ENUM_LABELS.get(v, str(v)))
    if kind == "boolean":
        return st.checkbox(label, value=bool(default), key=key, help=help_text)
    if kind in ("number", "integer"):
        lo, hi = spec.get("minimum"), spec.get("maximum")
        is_int = kind == "integer"
        if is_int and lo is not None and hi is not None and hi - lo <= 10:
            return st.slider(label, int(lo), int(hi), int(default if default is not None else hi), key=key, help=help_text)
        cast = int if is_int else float
        return st.number_input(
            label, min_value=cast(lo) if lo is not None else None, max_value=cast(hi) if hi is not None else None,
            value=cast(default) if default is not None else None, step=1 if is_int else 0.1, key=key,
            placeholder="введите значение", help=help_text,
        )
    if kind == "array":
        raw = st.text_input(f"{label} (через запятую)", key=key, help=help_text)
        return [x.strip() for x in raw.split(",") if x.strip()] or None
    if kind == "string":
        return st.text_input(label, value=default or "", key=key, help=help_text) or None
    return None  # сложные объекты в форме не показываем


with st.form(f"form_{source}_{tool.name}"):
    args: dict = {}
    if not props:
        st.caption("У инструмента нет параметров.")
    shown = props.items()
    if source == "remote":
        shown = [(n, s) for n, s in props.items() if n in required or n in REMOTE_LABELS]
    cols = st.columns(2)
    for i, (name, spec) in enumerate(shown):
        with cols[i % 2]:
            value = field_widget(name, spec, key=f"{source}_{tool.name}_{name}")
        if value is not None and value != "":
            args[name] = value
    submitted = st.form_submit_button("Рассчитать / выполнить", type="primary")

# ---------- вызов и карточка результата ----------


def risk_card(res: dict) -> None:
    bg, fg, label = RISK_STYLE.get(res.get("risk"), RISK_STYLE["info"])
    st.markdown(
        f"""<div style="background:{bg};border-left:8px solid {fg};border-radius:12px;padding:20px 24px;margin:8px 0">
        <div style="font-size:15px;color:{fg};font-weight:600;text-transform:uppercase;letter-spacing:1px">{label}</div>
        <div style="font-size:52px;font-weight:700;color:{fg};line-height:1.1">{res['value']}
        <span style="font-size:22px;font-weight:500">{res.get('unit', '')}</span></div>
        <div style="font-size:20px;color:#222;margin-top:6px">{res.get('interpretation', '')}</div></div>""",
        unsafe_allow_html=True,
    )
    for w in res.get("warnings", []):
        st.warning(w)
    left, right = st.columns(2)
    with left:
        st.subheader("Разбор расчёта")
        comps = res.get("components", {})
        st.dataframe(pd.DataFrame({"Компонент": list(comps), "Значение": [str(v) for v in comps.values()]}),
                     hide_index=True, width="stretch")
    with right:
        st.subheader("Рекомендации")
        for r in res.get("recommendations", []):
            st.markdown(f"- {r}")
        st.markdown(f"**Источник методики:** [{res.get('source')}]({res.get('source_url')})")
    st.caption(res.get("disclaimer", ""))


if submitted:
    try:
        with st.spinner(f"Вызов tools/call → {tool.name}…"):
            outcome = call(source, tool, args)
    except ToolError as exc:
        show_error("Инструмент вернул ошибку", exc.message)
        st.session_state[f"packets_{source}"] = getattr(exc, "packets", [])
        st.session_state[f"packets_title_{source}"] = f"tools/call {tool.name} — ошибка"
    except ServerUnavailable as exc:
        show_error("Сетевой сервер недоступен", str(exc))
    except RemoteTimeout as exc:
        show_error("Внешний сервер не ответил", str(exc))
    except Exception as exc:  # noqa: BLE001
        show_error("Непредвиденная ошибка, приложение продолжает работать", f"{type(exc).__name__}: {exc}")
    else:
        st.session_state[f"packets_{source}"] = outcome.packets
        st.session_state[f"packets_title_{source}"] = f"tools/call {tool.name} — {SOURCES[source]}"
        notes = getattr(outcome, "notes", [])
        if notes:
            with st.expander(f"Санитайзер исправил параметры: {len(notes)}", expanded=True):
                for n in notes:
                    st.markdown(f"- `{n}`")
        if getattr(outcome, "from_cache", False):
            st.info("Ответ взят из кэша — запрос к PubMed не повторялся.")
        res = outcome.structured or {}
        if "value" in res and "risk" in res:
            risk_card(res)
        elif "calculators" in res:
            st.subheader("Методики сервера")
            st.dataframe(pd.DataFrame(res["calculators"]).astype(str), hide_index=True, width="stretch")
        else:
            st.markdown(outcome.text or "_Пустой ответ_")
            if res:
                with st.expander("Структурированный ответ (structuredContent)"):
                    st.json(res)

# ---------- инспектор JSON-RPC ----------

# Последний обмен с текущим источником: tools/list при подключении, tools/call после расчёта.
packets = st.session_state.get(f"packets_{source}", [])
with st.expander(f"🔬 Инспектор протокола MCP (JSON-RPC): "
                 f"{st.session_state.get(f'packets_title_{source}', '—')}"):
    if not packets:
        st.caption("Пакетов пока нет. Сделайте вызов — здесь появятся сырые запросы и ответы.")
    for p in packets:
        body = p["json"]
        what = body.get("method") or ("result" if "result" in body else "error" if "error" in body else "")
        st.markdown(f"**{p['direction']}** · `{p['time']}` · `{what}` · id={body.get('id', '—')}")
        st.code(json.dumps(body, ensure_ascii=False, indent=2), language="json")
