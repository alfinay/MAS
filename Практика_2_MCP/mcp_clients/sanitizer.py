"""Автосанитизация параметров перед отправкой на сетевой сервер.

1. Приводит типы по JSON-схеме инструмента: «72,5» → 72.5, «да» → True,
   «ж» → "female".
2. Подставляет физиологически безопасные значения по умолчанию (Smart
   Defaults) для пропущенных необязательных параметров.
3. Возвращает список заметок: что именно было исправлено, чтобы врач видел.
"""

from typing import Any

# Безопасные значения по умолчанию: «норма» или «фактор риска отсутствует».
SMART_DEFAULTS: dict[str, Any] = {
    "eye": 4,
    "verbal": 5,
    "motor": 6,
    "respiratory_rate": 16,
    "systolic_bp": 120,
    "diastolic_bp": 80,
    "altered_mentation": False,
    "chf": False,
    "hypertension": False,
    "diabetes": False,
    "stroke_tia": False,
    "vascular_disease": False,
}

TRUE_WORDS = {"true", "1", "yes", "y", "да", "д", "+", "on"}
FALSE_WORDS = {"false", "0", "no", "n", "нет", "н", "-", "off", ""}
ENUM_SYNONYMS = {
    "м": "male", "муж": "male", "мужской": "male", "m": "male",
    "ж": "female", "жен": "female", "женский": "female", "f": "female",
}


def _to_number(value: Any, integer: bool) -> float | int:
    if isinstance(value, bool):
        raise ValueError("ожидалось число")
    if isinstance(value, str):
        value = value.strip().replace(",", ".").replace(" ", "")
    number = float(value)
    return int(round(number)) if integer else number


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in TRUE_WORDS:
        return True
    if text in FALSE_WORDS:
        return False
    raise ValueError(f"не похоже на да/нет: {value!r}")


def _to_enum(value: Any, options: list[Any]) -> Any:
    if value in options:
        return value
    text = str(value).strip().lower()
    text = ENUM_SYNONYMS.get(text, text)
    for option in options:
        if str(option).lower() == text:
            return option
    raise ValueError(f"допустимо одно из {options}")


def sanitize(arguments: dict[str, Any], schema: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Очистить аргументы по схеме. Возвращает (аргументы, заметки)."""
    props: dict[str, Any] = schema.get("properties", {})
    clean: dict[str, Any] = {}
    notes: list[str] = []

    for name, spec in props.items():
        raw = arguments.get(name)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            if name in SMART_DEFAULTS:
                clean[name] = SMART_DEFAULTS[name]
                notes.append(f"{name}: не указан → безопасное значение {SMART_DEFAULTS[name]!r}")
            continue  # обязательное без умолчания — сервер сам сообщит об ошибке
        try:
            if "enum" in spec:
                value = _to_enum(raw, spec["enum"])
            elif spec.get("type") in ("number", "integer"):
                value = _to_number(raw, spec.get("type") == "integer")
            elif spec.get("type") == "boolean":
                value = _to_bool(raw)
            else:
                value = raw
        except (TypeError, ValueError) as exc:
            if name in SMART_DEFAULTS:
                value = SMART_DEFAULTS[name]
                notes.append(f"{name}: {raw!r} не распознано ({exc}) → {value!r}")
            else:
                value = raw  # оставляем как есть: сервер вернёт понятную ошибку
                notes.append(f"{name}: {raw!r} не распознано ({exc})")
        if value != raw:
            notes.append(f"{name}: {raw!r} → {value!r}")
        clean[name] = value
    return clean, notes
