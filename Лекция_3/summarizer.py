# summarizer.py
"""Агент-суммаризатор.

``engine="sumy"`` строит резюме алгоритмом LSA из библиотеки Sumy.
``engine="ollama"`` / ``engine="openai"`` запрашивают модель через
OpenAI-совместимый endpoint из секций ``providers`` / ``llm`` в config.yaml.
"""

import logging
import os
from pathlib import Path

import nltk
import requests

# Импорты Sumy – чистый Python, без компилируемых расширений
from sumy.parsers.plaintext import PlaintextParser
from sumy.nlp.tokenizers import Tokenizer
from sumy.summarizers.lsa import LsaSummarizer

from utils import load_config

# Дочерний логгер – вывод настраивается у родителя «archiv_tracker» (utils.py)
logger = logging.getLogger("archiv_tracker.summarizer")

# Словарь NLTK для деления текста на предложения хранится в папке проекта:
# так он есть и на сервере, куда приложение выложено, без ручной установки.
NLTK_DIR = Path(__file__).parent / "nltk_data"
nltk.data.path.insert(0, str(NLTK_DIR))


def _ensure_punkt() -> None:
    """Скачать ``punkt_tab`` в ``nltk_data/``, если его ещё нет."""
    try:
        nltk.data.find("tokenizers/punkt_tab/english/")
    except LookupError:
        logger.info("Загрузка словаря NLTK punkt_tab в nltk_data/ …")
        nltk.download("punkt_tab", download_dir=str(NLTK_DIR), quiet=True)


class LlmUnavailable(Exception):
    """Локальный сервер модели не ответил."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


LLM_PROVIDERS = ("ollama", "openai")


def normalize_engine(engine: str | None) -> str:
    """``sumy``, ``ollama`` или ``openai``. Неизвестное значение становится ``sumy``."""
    name = str(engine or "sumy").strip().lower()
    if name not in ("sumy", *LLM_PROVIDERS):
        return "sumy"
    return name


def resolve_llm(cfg: dict | None = None, engine: str | None = None) -> dict:
    """Настройки модели для провайдера. Берёт блок ``providers``, иначе ``llm``."""
    cfg = load_config() if cfg is None else cfg
    engine = normalize_engine(engine if engine is not None else cfg.get("summarizer"))
    providers = cfg.get("providers") or {}
    stored = providers.get(engine)
    if isinstance(stored, dict):
        return dict(stored)
    return dict(cfg.get("llm") or {})


def llm_down_message(base_url: str, *, with_fallback: bool = True) -> str:
    target = base_url or "адрес не задан"
    text = f"Сервер LLM недоступен ({target})."
    if with_fallback:
        text += " Обработка резюме переключена на Sumy."
    return text


def llm_unavailable_message(llm: dict | None = None, *, with_fallback: bool = True) -> str | None:
    """Сообщение, если сервер модели не отвечает. Иначе None.

    Без ``llm`` читает активного провайдера из config.yaml. Для ``sumy`` возвращает None.
    """
    if llm is None:
        cfg = load_config()
        if normalize_engine(cfg.get("summarizer")) == "sumy":
            return None
        llm = resolve_llm(cfg)
    base_url = str(llm.get("base_url") or "").rstrip("/")
    if not base_url or not llm.get("model"):
        return llm_down_message(base_url, with_fallback=with_fallback)
    try:
        requests.get(f"{base_url}/models", timeout=3)
    except requests.RequestException:
        return llm_down_message(base_url, with_fallback=with_fallback)
    return None


def _server_down(exc: requests.RequestException) -> bool:
    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        return True
    response = getattr(exc, "response", None)
    if response is None:
        return True
    return response.status_code >= 500


def _get_summarizer() -> LsaSummarizer:
    """Создать экземпляр суммаризатора Sumy LSA.

    Объект лёгкий, поэтому создаём новый при каждом вызове – накладные
    расходы ничтожны при нашем небольшом числе публикаций.
    """
    summarizer = LsaSummarizer()
    return summarizer


def _summarize_lsa(text: str, max_sentences: int) -> str:
    """Построить резюме алгоритмом LSA (английский токенизатор)."""
    _ensure_punkt()
    parser = PlaintextParser.from_string(text, Tokenizer("english"))
    summary_sentences = _get_summarizer()(parser.document, max_sentences)
    summary = " ".join(str(sentence) for sentence in summary_sentences)
    logger.info("Резюме построено алгоритмом LSA (%d предложений).", max_sentences)
    return summary


def _summarize_llm(text: str, max_sentences: int, llm: dict) -> str:
    """Запросить резюме у модели через ``/v1/chat/completions``."""
    base_url = str(llm["base_url"]).rstrip("/")
    model = llm["model"]
    api_key = llm.get("api_key") or os.environ.get(str(llm.get("api_key_env") or ""), "ollama")
    response = requests.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": model,
            "temperature": 0.2,
            "max_tokens": max(64, max_sentences * 80),
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Сожми текст. "
                        f"Лимит предложений: {max_sentences}. "
                        "Пиши на языке исходного текста. "
                        "Только резюме, без вступления."
                    ),
                },
                {"role": "user", "content": text},
            ],
        },
        timeout=120,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    summary = (content or "").strip()
    logger.info("Резюме построено моделью %s (лимит %d).", model, max_sentences)
    return summary


def summarize(
    text: str,
    max_sentences: int = 3,
    engine: str = "sumy",
    llm: dict | None = None,
) -> str:
    """Вернуть краткое резюме текста *text*.

    Параметры
    ---------
    text: str
        Полный текст публикации.
    max_sentences: int, необязательный
        Максимальное число предложений в резюме. По умолчанию 3.
    engine: str, необязательный
        ``sumy`` (по умолчанию), ``ollama`` или ``openai``.
    llm: dict, необязательный
        Подключение модели. Если не задано, берётся из config.yaml.

    Возвращает
    ----------
    str
        Готовое резюме. Если выбрана модель, а сервер не отвечает,
        выбрасывается ``LlmUnavailable``.
    """
    if not text:
        logger.warning("Empty text received for summarization.")
        return ""

    engine = normalize_engine(engine)
    if engine != "sumy":
        if llm is None:
            llm = resolve_llm(engine=engine)
        base_url = str(llm.get("base_url") or "").rstrip("/")
        if not (base_url and llm.get("model")):
            raise LlmUnavailable(llm_down_message(base_url))
        try:
            summary = _summarize_llm(text, max_sentences, llm)
        except requests.RequestException as exc:
            if _server_down(exc):
                raise LlmUnavailable(llm_down_message(base_url)) from exc
            detail = ""
            resp = getattr(exc, "response", None)
            if resp is not None and resp.text:
                detail = f": {resp.text[:300]}"
            logger.warning(
                "Не удалось получить резюме от %s (%s%s). Используется Sumy.",
                llm.get("model"),
                exc,
                detail,
            )
            return _summarize_lsa(text, max_sentences)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            logger.warning(
                "Ответ модели %s не разобран (%s). Используется Sumy.",
                llm.get("model"),
                exc,
            )
            return _summarize_lsa(text, max_sentences)
        if summary:
            return summary
        logger.warning("Модель %s вернула пустое резюме. Используется Sumy.", llm.get("model"))
        return _summarize_lsa(text, max_sentences)

    return _summarize_lsa(text, max_sentences)
