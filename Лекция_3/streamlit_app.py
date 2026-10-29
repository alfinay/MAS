# streamlit_app.py
"""Streamlit-интерфейс мульти-агентного трекера публикаций archive.org.

Возможности:
- Настройки запуска находятся в боковой панели, провайдеры модели — на отдельной вкладке.
- Кнопка ручного запуска одного цикла обработки (поиск → извлечение текста → резюме).
- Найденные публикации и их резюме выводятся прямо в интерфейс.
- Схема агентов открывается на отдельной вкладке.
- Отправка на почту в тестовом режиме отключена.
"""

import subprocess
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

# Импорт основной функции обработки из main.py (файлы лежат в одной папке).
from main import process_items
from summarizer import llm_unavailable_message
from scraper import SOURCE_NAMES
from utils import HISTORY_PATH, get_history, load_config, resolve_date_range, save_model_settings

PROVIDER_LABELS = {
    "sumy": "Sumy",
    "ollama": "Ollama",
    "openai": "OpenAI-совместимый",
}
PROVIDER_DEFAULTS = {
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "model": "qwen2.5:7b",
        "api_key_env": "",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "api_key_env": "OPENAI_API_KEY",
    },
}


def _drafts_from_config(cfg: dict) -> dict:
    stored = cfg.get("providers") or {}
    llm = cfg.get("llm") or {}
    active = str(cfg.get("summarizer", "sumy")).strip().lower()
    drafts = {}
    for name, defaults in PROVIDER_DEFAULTS.items():
        draft = dict(defaults)
        saved = stored.get(name)
        source = saved if isinstance(saved, dict) and saved else (llm if active == name else {})
        for key in ("base_url", "model", "api_key_env"):
            if source.get(key):
                draft[key] = source[key]
        drafts[name] = draft
    return drafts


def _field(name: str, field: str) -> str:
    key = f"{field}_{name}"
    if key in st.session_state:
        return str(st.session_state[key] or "")
    return str(st.session_state["provider_drafts"][name].get(field) or "")


def _active_llm() -> dict:
    name = st.session_state["provider"]
    if name == "sumy":
        return {}
    data = {
        "base_url": _field(name, "base_url").strip(),
        "model": _field(name, "model").strip(),
        "api_key_env": _field(name, "api_key_env").strip(),
    }
    secret = _field(name, "api_key").strip()
    if secret:
        data["api_key"] = secret
    return data


def _collect_providers() -> dict:
    providers = {}
    for name in PROVIDER_DEFAULTS:
        entry = {
            "base_url": _field(name, "base_url").strip(),
            "model": _field(name, "model").strip(),
        }
        env_name = _field(name, "api_key_env").strip()
        if env_name:
            entry["api_key_env"] = env_name
        providers[name] = entry
    return providers


def _provider_input(label: str, name: str, field: str, **kwargs):
    key = f"{field}_{name}"
    if key not in st.session_state:
        st.session_state[key] = st.session_state["provider_drafts"][name].get(field) or ""
    st.text_input(label, key=key, **kwargs)

st.set_page_config(
    page_title="Трекер публикаций",
    page_icon="📚",
    layout="wide",
)

cfg = load_config()
if "provider_drafts" not in st.session_state:
    st.session_state["provider_drafts"] = _drafts_from_config(cfg)
    for name in PROVIDER_DEFAULTS:
        for field in ("base_url", "model", "api_key_env", "api_key"):
            st.session_state.pop(f"{field}_{name}", None)
if "provider" not in st.session_state:
    saved_provider = str(cfg.get("summarizer", "sumy")).strip().lower()
    st.session_state["provider"] = saved_provider if saved_provider in PROVIDER_LABELS else "sumy"

# ---------------------------------------------------------------------------
# Боковая панель: настройки
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("⚙️ Настройки")

    st.subheader("Поиск")
    topics_text = st.text_area(
        "Темы для отслеживания (по одной в строке)",
        value="\n".join(cfg.get("topics", [])),
        height=120,
        help="По умолчанию берутся из config.yaml. Изменения действуют только в этом сеансе.",
    )
    topics = [t.strip() for t in topics_text.splitlines() if t.strip()]

    available_sources = list(SOURCE_NAMES)
    default_sources = [
        sid for sid in cfg.get("sources", available_sources) if sid in SOURCE_NAMES
    ] or available_sources
    sources = st.multiselect(
        "Подключённые источники",
        options=available_sources,
        default=default_sources,
        format_func=lambda sid: SOURCE_NAMES.get(sid, sid),
        help=(
            "Какие агенты-источники опрашивать. "
            "По умолчанию берутся из config.yaml. Изменения действуют только в этом сеансе."
        ),
    )

    period = st.selectbox(
        "Диапазон дат",
        options=["week", "month", "custom"],
        index=0,
        format_func=lambda key: {
            "week": "Неделя",
            "month": "Месяц",
            "custom": "Произвольный",
        }[key],
        help=(
            "Ограничение по дате в поисковом запросе. "
            "Неделя — последние 7 дней, месяц — с этой даты месяц назад. "
            "Обе границы включаются. По умолчанию — неделя."
        ),
    )
    if period == "custom":
        default_from, default_to = resolve_date_range(preset="week")
        date_from = st.date_input("С", value=default_from)
        date_to = st.date_input("По", value=default_to)
    else:
        date_from, date_to = resolve_date_range(preset=period)
        st.caption(
            f"{date_from.strftime('%d.%m.%Y')} — {date_to.strftime('%d.%m.%Y')}"
        )
    dates_ok = bool(date_from and date_to and date_from <= date_to)
    if not dates_ok:
        st.warning("Дата «С» должна быть не позже даты «По».")

    rows = st.slider(
        "Сколько последних записей запрашивать",
        min_value=5, max_value=100, value=20, step=5,
    )

    st.subheader("Резюме")
    engine = st.session_state["provider"]
    if engine == "sumy":
        provider_caption = "Провайдер: Sumy"
    else:
        provider_caption = (
            f"Провайдер: {PROVIDER_LABELS[engine]} · {_field(engine, 'model') or '—'}"
        )
    st.caption(f"{provider_caption}. Меняется на вкладке «Провайдеры».")
    max_sentences = st.slider(
        "Предложений в резюме",
        1,
        10,
        3,
        help=(
            "Сколько предложений вырезать из текста."
            if engine == "sumy"
            else "Лимит предложений в инструкции для модели."
        ),
    )
    text_limit = st.slider(
        "Лимит текста страницы (символов)",
        min_value=500, max_value=10000, value=2000, step=500,
        help="Сколько символов текста со страницы передавать агенту-суммаризатору.",
    )

    st.subheader("История")
    skip_seen = st.checkbox(
        "Пропускать уже просмотренные",
        value=False,
        help="Если включено – публикации из history.json не показываются повторно.",
    )
    save_history = st.checkbox(
        "Сохранять найденное в историю",
        value=False,
        help="Для проверки лучше выключить, чтобы одни и те же результаты можно было получать снова.",
    )
    st.caption(f"В истории сейчас записей: **{len(get_history())}**")
    if st.button("🗑 Очистить историю", use_container_width=True):
        HISTORY_PATH.write_text("[]", encoding="utf-8")
        st.success("История очищена.")
        st.rerun()

    st.subheader("Почта")
    st.checkbox(
        "Отправлять резюме на почту",
        value=False,
        disabled=True,
        help="Отключено на время проверки. Результаты выводятся только в интерфейс.",
    )
    st.caption(f"Получатель: {cfg.get('email', {}).get('to_address', '—')}")

    st.subheader("Планировщик")
    st.caption(
        f"Интервал автоматической проверки: "
        f"**{cfg.get('check_interval_seconds', 3600)} с** (задаётся в config.yaml)"
    )

    with st.expander("Полная конфигурация (config.yaml)"):
        safe_cfg = dict(cfg)
        if "email" in safe_cfg:
            safe_cfg["email"] = {
                k: ("••••••" if k == "password" else v)
                for k, v in safe_cfg["email"].items()
            }
        st.json(safe_cfg)

# ---------------------------------------------------------------------------
# Основная область
# ---------------------------------------------------------------------------
st.title("📚 Трекер публикаций")
st.caption(
    "Мульти-агентная система: скрапер → валидатор → извлечение текста → суммаризатор. "
    "🧪 Тестовый режим: письма не отправляются."
)

tab_search, tab_providers, tab_diagram = st.tabs(
    ["🔎 Поиск", "🤖 Провайдеры", "🗺 Схема агентов"]
)

with tab_search:
    run = st.button(
        "🚀 Запустить поиск сейчас",
        type="primary",
        disabled=not topics or not sources or not dates_ok,
    )
    if not topics:
        st.warning("Укажите хотя бы одну тему в боковой панели.")
    elif not sources:
        st.warning("Подключите хотя бы один источник в боковой панели.")
    elif not dates_ok:
        st.warning("В боковой панели дата «С» должна быть не позже даты «По».")

    if run:
        with st.spinner("Агенты работают: ищем публикации и составляем резюме…"):
            try:
                st.session_state["llm_notice"] = None
                st.session_state["results"] = process_items(
                    topics=topics,
                    sources=sources,
                    rows=rows,
                    skip_seen=skip_seen,
                    save_history=save_history,
                    max_sentences=max_sentences,
                    text_limit=text_limit,
                    engine=engine,
                    llm=_active_llm() or None,
                    date_from=date_from,
                    date_to=date_to,
                    send=False,  # пока только вывод в интерфейс
                )
                st.session_state["run_error"] = None
                st.session_state["llm_notice"] = process_items.last_llm_notice
            except Exception as e:
                st.session_state["results"] = []
                st.session_state["run_error"] = str(e)

    if st.session_state.get("run_error"):
        st.error(f"Ошибка при обработке: {st.session_state['run_error']}")
    if st.session_state.get("llm_notice"):
        st.warning(st.session_state["llm_notice"])

    results = st.session_state.get("results")
    if results is not None and not st.session_state.get("run_error"):
        st.header("Найденные публикации")
        with_summary = sum(1 for r in results if r.get("summary"))
        c1, c2, c3 = st.columns(3)
        c1.metric("Найдено", len(results))
        c2.metric("С резюме", with_summary)
        c3.metric("Отправлено на почту", 0)

        if not results:
            st.info(
                "Новых релевантных публикаций не найдено. "
                "Попробуйте выключить «Пропускать уже просмотренные» или изменить темы."
            )
        else:
            tab_cards, tab_table = st.tabs(["🗂 Карточки", "📋 Таблица"])
            with tab_cards:
                for i, r in enumerate(results, start=1):
                    title = r.get("title") or "(без названия)"
                    if isinstance(title, list):
                        title = " ".join(title)
                    with st.expander(f"{i}. {title}", expanded=i <= 3):
                        src = SOURCE_NAMES.get(r.get("source"), r.get("source") or "—")
                        st.markdown(f"🔗 [{r['url']}]({r['url']})")
                        st.markdown(f"**Источник:** {src}")
                        st.markdown(f"**Идентификатор:** `{r['identifier']}`")
                        if r.get("journal"):
                            st.markdown(f"**Журнал:** {r['journal']}")
                        if r.get("authors"):
                            st.markdown(f"**Авторы:** {r['authors']}")
                        if r.get("published"):
                            st.markdown(f"**Дата:** {r['published']}")
                        st.markdown("**Резюме:**")
                        if r.get("summary"):
                            st.write(r["summary"])
                        else:
                            st.caption("Резюме отсутствует.")
                        st.caption(f"Статус: {r.get('status', '')}")
            with tab_table:
                df = pd.DataFrame(results).rename(
                    columns={
                        "source": "Источник",
                        "identifier": "Идентификатор",
                        "title": "Название",
                        "url": "Ссылка",
                        "summary": "Резюме",
                        "status": "Статус",
                    }
                )
                if "Источник" in df.columns:
                    df["Источник"] = df["Источник"].map(
                        lambda sid: SOURCE_NAMES.get(sid, sid)
                    )
                st.dataframe(
                    df,
                    use_container_width=True,
                    hide_index=True,
                    column_config={"Ссылка": st.column_config.LinkColumn("Ссылка")},
                )
    elif results is None:
        st.info("Нажмите «Запустить поиск сейчас», чтобы агенты выполнили один цикл обработки.")

    with st.expander("📜 Последние записи журнала"):
        log_path = Path(__file__).parent / "main.log"
        if log_path.exists():
            try:
                tail = subprocess.check_output(["tail", "-n", "20", str(log_path)])
                st.text(tail.decode())
            except Exception as exc:
                st.warning(f"Не удалось прочитать файл журнала: {exc}")
        else:
            st.caption("Файл журнала не найден — записи выводятся в терминал, где запущен Streamlit.")

with tab_providers:
    st.header("Провайдеры модели")
    st.caption(
        "Выбранный провайдер строит резюме в этом сеансе сразу. "
        "Планировщик читает config.yaml после сохранения."
    )
    st.radio(
        "Активный провайдер",
        options=list(PROVIDER_LABELS),
        format_func=lambda key: PROVIDER_LABELS[key],
        horizontal=True,
        key="provider",
    )
    current = st.session_state["provider"]
    if current == "sumy":
        st.info(
            "Sumy строит резюме алгоритмом LSA на этой машине и не вызывает языковую модель."
        )
    else:
        _provider_input("Адрес API", current, "base_url")
        _provider_input("Модель", current, "model")
        _provider_input(
            "Переменная окружения с ключом",
            current,
            "api_key_env",
            help="Для Ollama поле можно оставить пустым. Сам ключ в файл не записывается.",
        )
        _provider_input(
            "Ключ для этого сеанса",
            current,
            "api_key",
            type="password",
            help="Попадает только в текущий процесс. В config.yaml записывается имя переменной.",
        )
        if st.button("Проверить подключение", key="check_provider"):
            notice = llm_unavailable_message(_active_llm(), with_fallback=False)
            model_name = st.session_state.get(f"model_{current}", "")
            st.session_state["provider_check"] = notice or f"Сервер отвечает. Модель: {model_name}."
            st.session_state["provider_check_ok"] = notice is None
            st.session_state["provider_check_for"] = current
        if (
            st.session_state.get("provider_check_for") == current
            and st.session_state.get("provider_check")
        ):
            if st.session_state.get("provider_check_ok"):
                st.success(st.session_state["provider_check"])
            else:
                st.warning(st.session_state["provider_check"])

    if st.button("Сохранить в config.yaml", type="primary", key="save_providers"):
        save_model_settings(st.session_state["provider"], _collect_providers())
        st.success("Провайдеры сохранены в config.yaml.")

with tab_diagram:
    diagram_path = Path(__file__).parent / "diagr" / "agents.html"
    if not diagram_path.is_file():
        st.warning("Файл схемы diagr/agents.html не найден.")
    else:
        components.html(
            diagram_path.read_text(encoding="utf-8"),
            height=900,
            scrolling=True,
        )
