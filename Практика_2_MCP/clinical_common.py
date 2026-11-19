"""Общий формат результата для калькуляторов обоих серверов.

Каждый инструмент возвращает словарь одного вида, чтобы интерфейс мог
нарисовать карточку: главное значение, цвет риска, разбор, рекомендации
и ссылку на первоисточник.
"""

import functools
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

# Цвета риска для интерфейса: зелёный / жёлтый / красный, плюс «справочно».
RISK_LEVELS = ("green", "yellow", "red", "info")


def make_result(
    *,
    calculator: str,
    value: float | int,
    unit: str,
    risk: str,
    interpretation: str,
    components: dict[str, Any],
    recommendations: list[str],
    source: str,
    source_url: str,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Собрать результат калькулятора в едином формате."""
    if risk not in RISK_LEVELS:
        raise ValueError(f"Неизвестный уровень риска: {risk}")
    return {
        "calculator": calculator,
        "value": round(value, 2) if isinstance(value, float) else value,
        "unit": unit,
        "risk": risk,
        "interpretation": interpretation,
        "components": components,
        "recommendations": recommendations,
        "source": source,
        "source_url": source_url,
        "warnings": warnings or [],
        "disclaimer": "Справочный расчёт. Не заменяет решение врача.",
    }


def value_errors_as_tool_errors(fn):
    """ValueError внутри калькулятора → ToolError с текстом для пользователя.

    Без этого SDK скрывает текст неожиданного исключения и клиент видит
    только «ошибка инструмента».
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

    return wrapper
