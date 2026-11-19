"""Сервер 2. Локальный сетевой MCP-сервер по транспорту SSE.

Запуск:  python -m clinical_net.server        (слушает http://127.0.0.1:8000)

Эндпоинты SSE:
  GET  /sse                          — открыть сессию и подписаться на события;
  POST /messages/?session_id=...     — отправить JSON-RPC запрос в эту сессию.

Шесть калькуляторов по нозологиям: кардиология, нефрология, реанимация,
эндокринология. Значения за физиологическими границами не роняют сервер:
расчёт выполняется, а в ответ добавляется предупреждение. Невозможные
значения (ноль, отрицательные) возвращаются как понятная ошибка инструмента.
"""

import logging
import sys
from typing import Annotated, Any, Literal

from pydantic import Field

from mcp.server import MCPServer

from clinical_common import make_result, value_errors_as_tool_errors

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [sse-server] %(levelname)s %(message)s",
)
log = logging.getLogger("clinical_net")

HOST, PORT = "127.0.0.1", 8000

mcp = MCPServer(
    name="clinical-net",
    title="Клинические шкалы (локальный SSE-сервер)",
    instructions="Калькуляторы по нозологиям. Справочно, не заменяет врача.",
)

# Физиологические границы: (минимум, максимум, единицы).
LIMITS = {
    "age": (18, 110, "лет"),
    "weight_kg": (30, 250, "кг"),
    "creatinine_mg_dl": (0.2, 15, "мг/дл"),
    "respiratory_rate": (6, 60, "в минуту"),
    "systolic_bp": (50, 260, "мм рт. ст."),
    "diastolic_bp": (25, 160, "мм рт. ст."),
    "glucose_mmol": (2, 30, "ммоль/л"),
    "insulin_uU_ml": (1, 300, "мкЕд/мл"),
}


def check(name: str, value: float, label: str, warnings: list[str]) -> None:
    """Проверить границы. Невозможное — ошибка, необычное — предупреждение."""
    if value <= 0:
        raise ValueError(f"{label}: значение {value} невозможно, должно быть больше нуля")
    lo, hi, unit = LIMITS[name]
    if not lo <= value <= hi:
        warnings.append(
            f"{label} = {value} {unit} вне типичного диапазона {lo}–{hi} {unit}. "
            "Проверьте ввод: результат может быть недостоверным."
        )


CATALOG = [
    {"name": "cha2ds2_vasc", "title_ru": "CHA₂DS₂-VASc: риск инсульта при фибрилляции предсердий",
     "title_en": "CHA2DS2-VASc", "area": "Кардиология",
     "parameters": {"age": "лет", "sex": "male/female", "chf, hypertension, diabetes, stroke_tia, vascular_disease": "да/нет"},
     "source": "Lip G.Y.H. et al. Chest, 2010. DOI 10.1378/chest.09-1584"},
    {"name": "mean_arterial_pressure", "title_ru": "Среднее артериальное давление (САД)",
     "title_en": "Mean Arterial Pressure (MAP)", "area": "Кардиология / реанимация",
     "parameters": {"systolic_bp": "50–260 мм рт. ст.", "diastolic_bp": "25–160 мм рт. ст."},
     "source": "Surviving Sepsis Campaign 2021. DOI 10.1097/CCM.0000000000005337"},
    {"name": "cockcroft_gault", "title_ru": "Клиренс креатинина (Кокрофт — Голт)",
     "title_en": "Cockcroft-Gault creatinine clearance", "area": "Нефрология",
     "parameters": {"age": "18–110 лет", "weight_kg": "30–250 кг", "creatinine_mg_dl": "0,2–15 мг/дл", "sex": "male/female"},
     "source": "Cockcroft D.W., Gault M.H. Nephron, 1976. DOI 10.1159/000180580"},
    {"name": "glasgow_coma_scale", "title_ru": "Шкала комы Глазго (ШКГ)",
     "title_en": "Glasgow Coma Scale (GCS)", "area": "Реанимация / неврология",
     "parameters": {"eye": "1–4", "verbal": "1–5", "motor": "1–6"},
     "source": "Teasdale G., Jennett B. Lancet, 1974. DOI 10.1016/S0140-6736(74)91639-0"},
    {"name": "qsofa", "title_ru": "qSOFA: быстрая оценка риска при сепсисе",
     "title_en": "quick SOFA", "area": "Реанимация",
     "parameters": {"respiratory_rate": "6–60 в минуту", "systolic_bp": "50–260 мм рт. ст.", "altered_mentation": "да/нет"},
     "source": "Seymour C.W. et al. JAMA, 2016. DOI 10.1001/jama.2016.0288"},
    {"name": "homa_ir", "title_ru": "Индекс инсулинорезистентности HOMA-IR",
     "title_en": "HOMA-IR", "area": "Эндокринология",
     "parameters": {"glucose_mmol": "2–30 ммоль/л натощак", "insulin_uU_ml": "1–300 мкЕд/мл натощак"},
     "source": "Matthews D.R. et al. Diabetologia, 1985. DOI 10.1007/BF00280883"},
]


@mcp.tool(title="Список калькуляторов")
def list_calculators() -> dict[str, Any]:
    """Мета-инструмент: методики по нозологиям, границы параметров и единицы.

    Ключевые слова: список, справка, методики, catalog.
    """
    return {"server": "clinical-net", "calculators": CATALOG}


@mcp.tool(title="CHA₂DS₂-VASc — риск инсульта при ФП (кардиология)")
@value_errors_as_tool_errors
def cha2ds2_vasc(
    age: Annotated[float, Field(description="Возраст, лет")],
    sex: Annotated[Literal["male", "female"], Field(description="Пол")],
    chf: Annotated[bool, Field(description="Хроническая сердечная недостаточность")] = False,
    hypertension: Annotated[bool, Field(description="Артериальная гипертензия")] = False,
    diabetes: Annotated[bool, Field(description="Сахарный диабет")] = False,
    stroke_tia: Annotated[bool, Field(description="Инсульт, ТИА или тромбоэмболия в анамнезе")] = False,
    vascular_disease: Annotated[bool, Field(description="Сосудистое заболевание (ИМ, атеросклероз)")] = False,
) -> dict[str, Any]:
    """CHA2DS2-VASc: риск инсульта при фибрилляции предсердий, 0–9 баллов.

    Ключевые слова: сердце, кардиология, инсульт, фибрилляция, AF, stroke, heart.
    """
    warnings: list[str] = []
    check("age", age, "Возраст", warnings)
    parts = {
        "ХСН (C)": int(chf),
        "Гипертензия (H)": int(hypertension),
        "Возраст ≥75 (A₂)": 2 if age >= 75 else 0,
        "Диабет (D)": int(diabetes),
        "Инсульт/ТИА (S₂)": 2 if stroke_tia else 0,
        "Сосудистое заболевание (V)": int(vascular_disease),
        "Возраст 65–74 (A)": 1 if 65 <= age < 75 else 0,
        "Женский пол (Sc)": 1 if sex == "female" else 0,
    }
    score = sum(parts.values())
    base = score - parts["Женский пол (Sc)"]  # пол сам по себе риск не повышает
    if base == 0:
        risk, text, recs = "green", "Низкий риск", ["Антикоагулянты не показаны"]
    elif base == 1:
        risk, text, recs = "yellow", "Промежуточный риск", ["Рассмотреть антикоагулянты (ESC 2020, класс IIa)"]
    else:
        risk, text, recs = "red", "Высокий риск", ["Антикоагулянты рекомендованы (ESC 2020, класс I)"]
    recs.append("Оценить риск кровотечения (HAS-BLED)")
    return make_result(
        calculator="cha2ds2_vasc", value=score, unit="баллов из 9", risk=risk,
        interpretation=text, components=parts, recommendations=recs,
        source="Lip G.Y.H. et al. Chest, 2010", source_url="https://doi.org/10.1378/chest.09-1584",
        warnings=warnings,
    )


@mcp.tool(title="Среднее артериальное давление (САД, MAP)")
@value_errors_as_tool_errors
def mean_arterial_pressure(
    systolic_bp: Annotated[float, Field(description="Систолическое давление, мм рт. ст.")],
    diastolic_bp: Annotated[float, Field(description="Диастолическое давление, мм рт. ст.")],
) -> dict[str, Any]:
    """MAP = (САД + 2 × ДАД) / 3. Цель при септическом шоке — не ниже 65 мм рт. ст.

    Ключевые слова: давление, гемодинамика, сепсис, blood pressure, MAP.
    """
    warnings: list[str] = []
    check("systolic_bp", systolic_bp, "Систолическое давление", warnings)
    check("diastolic_bp", diastolic_bp, "Диастолическое давление", warnings)
    if diastolic_bp >= systolic_bp:
        warnings.append("Диастолическое давление не меньше систолического — проверьте ввод.")
    value = (systolic_bp + 2 * diastolic_bp) / 3
    if value < 65:
        risk, text, recs = "red", "Гипоперфузия органов", ["Срочная оценка гемодинамики", "Цель MAP ≥ 65"]
    elif value <= 100:
        risk, text, recs = "green", "Норма (70–100)", ["Наблюдение"]
    else:
        risk, text, recs = "yellow", "Повышенное", ["Контроль давления, оценка гипертензии"]
    return make_result(
        calculator="mean_arterial_pressure", value=value, unit="мм рт. ст.", risk=risk,
        interpretation=text,
        components={"САД": systolic_bp, "ДАД": diastolic_bp, "Формула": "(САД + 2·ДАД) / 3"},
        recommendations=recs,
        source="Evans L. et al. Surviving Sepsis Campaign, Crit Care Med 2021",
        source_url="https://doi.org/10.1097/CCM.0000000000005337",
        warnings=warnings,
    )


@mcp.tool(title="Клиренс креатинина Кокрофт — Голт (нефрология)")
@value_errors_as_tool_errors
def cockcroft_gault(
    age: Annotated[float, Field(description="Возраст, лет")],
    weight_kg: Annotated[float, Field(description="Масса тела, кг")],
    creatinine_mg_dl: Annotated[float, Field(description="Креатинин сыворотки, мг/дл")],
    sex: Annotated[Literal["male", "female"], Field(description="Пол")],
) -> dict[str, Any]:
    """Cockcroft-Gault: клиренс креатинина, мл/мин. Используется для дозирования лекарств.

    Ключевые слова: почки, нефрология, креатинин, дозы, kidney, clearance.
    """
    warnings: list[str] = []
    check("age", age, "Возраст", warnings)
    check("weight_kg", weight_kg, "Масса тела", warnings)
    check("creatinine_mg_dl", creatinine_mg_dl, "Креатинин", warnings)
    if age >= 140:
        raise ValueError("Возраст 140 и больше делает формулу бессмысленной")
    factor = 0.85 if sex == "female" else 1.0
    value = (140 - age) * weight_kg / (72 * creatinine_mg_dl) * factor
    if value >= 60:
        risk, text, recs = "green", "Нормальная или слегка сниженная функция", ["Обычные дозы препаратов"]
    elif value >= 30:
        risk, text, recs = "yellow", "Умеренное снижение", ["Проверить дозы препаратов, выводимых почками"]
    else:
        risk, text, recs = "red", "Выраженное снижение", ["Коррекция доз обязательна", "Консультация нефролога"]
    return make_result(
        calculator="cockcroft_gault", value=value, unit="мл/мин", risk=risk,
        interpretation=text,
        components={"(140 − возраст)": 140 - age, "Масса, кг": weight_kg,
                    "72 × креатинин": round(72 * creatinine_mg_dl, 2), "Коэффициент пола": factor},
        recommendations=recs,
        source="Cockcroft D.W., Gault M.H. Nephron, 1976", source_url="https://doi.org/10.1159/000180580",
        warnings=warnings,
    )


@mcp.tool(title="Шкала комы Глазго (ШКГ, GCS) — реанимация")
@value_errors_as_tool_errors
def glasgow_coma_scale(
    eye: Annotated[int, Field(description="Открывание глаз, 1–4")] = 4,
    verbal: Annotated[int, Field(description="Речевая реакция, 1–5")] = 5,
    motor: Annotated[int, Field(description="Двигательная реакция, 1–6")] = 6,
) -> dict[str, Any]:
    """Glasgow Coma Scale: уровень сознания, 3–15 баллов.

    Ключевые слова: кома, сознание, реанимация, неврология, GCS, coma.
    """
    for label, v, hi in (("Открывание глаз", eye, 4), ("Речь", verbal, 5), ("Движения", motor, 6)):
        if not 1 <= v <= hi:
            raise ValueError(f"{label}: {v} — допустимо от 1 до {hi}")
    score = eye + verbal + motor
    if score >= 13:
        risk, text, recs = "green", "Лёгкое нарушение сознания или норма", ["Динамическое наблюдение"]
    elif score >= 9:
        risk, text, recs = "yellow", "Умеренное нарушение сознания", ["Наблюдение в палате интенсивной терапии", "КТ головы"]
    else:
        risk, text, recs = "red", "Тяжёлое нарушение, кома", ["Защита дыхательных путей (интубация при ≤ 8)", "Срочная КТ"]
    return make_result(
        calculator="glasgow_coma_scale", value=score, unit="баллов из 15", risk=risk,
        interpretation=text, components={"Глаза (E)": eye, "Речь (V)": verbal, "Движения (M)": motor},
        recommendations=recs,
        source="Teasdale G., Jennett B. Lancet, 1974",
        source_url="https://doi.org/10.1016/S0140-6736(74)91639-0",
    )


@mcp.tool(title="qSOFA — риск неблагоприятного исхода при сепсисе")
@value_errors_as_tool_errors
def qsofa(
    respiratory_rate: Annotated[float, Field(description="Частота дыхания, в минуту")],
    systolic_bp: Annotated[float, Field(description="Систолическое давление, мм рт. ст.")],
    altered_mentation: Annotated[bool, Field(description="Изменённое сознание (ШКГ < 15)")] = False,
) -> dict[str, Any]:
    """quick SOFA: 0–3 балла, 2 и больше — высокий риск при подозрении на инфекцию.

    Ключевые слова: сепсис, инфекция, реанимация, sepsis.
    """
    warnings: list[str] = []
    check("respiratory_rate", respiratory_rate, "Частота дыхания", warnings)
    check("systolic_bp", systolic_bp, "Систолическое давление", warnings)
    parts = {
        "ЧДД ≥ 22": int(respiratory_rate >= 22),
        "САД ≤ 100": int(systolic_bp <= 100),
        "Изменённое сознание": int(altered_mentation),
    }
    score = sum(parts.values())
    if score >= 2:
        risk, text, recs = "red", "Высокий риск: вероятна органная дисфункция", ["Оценить полную шкалу SOFA", "Лактат, посевы, антибиотики по протоколу"]
    elif score == 1:
        risk, text, recs = "yellow", "Промежуточный риск", ["Повторная оценка, наблюдение"]
    else:
        risk, text, recs = "green", "Низкий риск по qSOFA", ["qSOFA не исключает сепсис — клиническая оценка"]
    return make_result(
        calculator="qsofa", value=score, unit="баллов из 3", risk=risk, interpretation=text,
        components=parts, recommendations=recs,
        source="Seymour C.W. et al. JAMA, 2016", source_url="https://doi.org/10.1001/jama.2016.0288",
        warnings=warnings,
    )


@mcp.tool(title="HOMA-IR — инсулинорезистентность (эндокринология)")
@value_errors_as_tool_errors
def homa_ir(
    glucose_mmol: Annotated[float, Field(description="Глюкоза натощак, ммоль/л")],
    insulin_uU_ml: Annotated[float, Field(description="Инсулин натощак, мкЕд/мл")],
) -> dict[str, Any]:
    """HOMA-IR = глюкоза × инсулин / 22,5.

    Ключевые слова: инсулин, диабет, глюкоза, эндокринология, insulin, diabetes.
    """
    warnings: list[str] = []
    check("glucose_mmol", glucose_mmol, "Глюкоза", warnings)
    check("insulin_uU_ml", insulin_uU_ml, "Инсулин", warnings)
    value = glucose_mmol * insulin_uU_ml / 22.5
    if value < 2.5:
        risk, text, recs = "green", "Инсулинорезистентности нет", ["Поддерживать режим питания и активности"]
    elif value < 3.5:
        risk, text, recs = "yellow", "Пограничное значение", ["Снизить быстрые углеводы, больше движения", "Оценить FINDRISC"]
    else:
        risk, text, recs = "red", "Инсулинорезистентность", ["Консультация эндокринолога", "Анализ HbA1c"]
    return make_result(
        calculator="homa_ir", value=value, unit="", risk=risk, interpretation=text,
        components={"Глюкоза, ммоль/л": glucose_mmol, "Инсулин, мкЕд/мл": insulin_uU_ml, "Делитель": 22.5},
        recommendations=recs,
        source="Matthews D.R. et al. Diabetologia, 1985", source_url="https://doi.org/10.1007/BF00280883",
        warnings=warnings,
    )


def main() -> None:
    log.info("SSE-сервер на http://%s:%s/sse", HOST, PORT)
    mcp.run("sse", host=HOST, port=PORT)


if __name__ == "__main__":
    main()
