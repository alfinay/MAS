"""Сервер 1. Локальный MCP-сервер по транспорту stdio.

Запуск:  python -m clinical_stdio.server

Сервер читает JSON-RPC 2.0 из stdin и пишет ответы в stdout. Поэтому в
stdout нельзя печатать ничего, кроме протокола: весь журнал идёт в stderr.

Тема калькуляторов — профилактика и питание: ИМТ, риск диабета FINDRISC,
функция почек CKD-EPI 2021 и суточная потребность в энергии.
"""

import logging
import sys
from typing import Annotated, Any, Literal

from pydantic import Field

from mcp.server import MCPServer

from clinical_common import make_result, value_errors_as_tool_errors

# Журнал строго в stderr: stdout занят протоколом.
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [stdio-server] %(levelname)s %(message)s",
)
log = logging.getLogger("clinical_stdio")

mcp = MCPServer(
    name="clinical-stdio",
    title="Калькуляторы профилактики (stdio)",
    instructions="Детерминированные медицинские калькуляторы. Справочно, не заменяет врача.",
)

Sex = Literal["male", "female"]

# Описание методик для мета-инструмента list_calculators.
CATALOG = [
    {
        "name": "bmi",
        "title_ru": "Индекс массы тела (ИМТ)",
        "title_en": "Body Mass Index (BMI)",
        "area": "Эндокринология / питание",
        "description": "Масса тела, делённая на квадрат роста. Классификация ВОЗ.",
        "parameters": {
            "weight_kg": "масса тела, кг, 20–300",
            "height_cm": "рост, см, 100–250",
        },
        "source": "WHO Technical Report Series 894, 2000. PMID 11234459",
    },
    {
        "name": "findrisc",
        "title_ru": "FINDRISC: риск диабета 2 типа за 10 лет",
        "title_en": "Finnish Diabetes Risk Score",
        "area": "Эндокринология / профилактика",
        "description": "Анкета из 8 вопросов, 0–26 баллов. Оценивает риск диабета 2 типа на 10 лет.",
        "parameters": {
            "age": "возраст, лет, 18–120",
            "bmi": "ИМТ, кг/м², 10–80",
            "waist_cm": "окружность талии, см, 40–200",
            "sex": "male / female",
            "physical_activity_daily": "не менее 30 минут активности в день",
            "vegetables_daily": "овощи, фрукты или ягоды каждый день",
            "antihypertensive_drugs": "принимает препараты от давления",
            "high_glucose_history": "когда-либо находили повышенный сахар",
            "family_history": "none / second_degree / first_degree",
        },
        "source": "Lindström J., Tuomilehto J. Diabetes Care, 2003. DOI 10.2337/diacare.26.3.725",
    },
    {
        "name": "ckd_epi_2021",
        "title_ru": "СКФ по креатинину (CKD-EPI 2021)",
        "title_en": "eGFR CKD-EPI 2021",
        "area": "Нефрология",
        "description": "Расчётная скорость клубочковой фильтрации без поправки на расу. Стадии по KDIGO.",
        "parameters": {
            "creatinine": "креатинин сыворотки",
            "creatinine_unit": "umol/L или mg/dL",
            "age": "возраст, лет, 18–120",
            "sex": "male / female",
        },
        "source": "Inker L.A. et al. NEJM, 2021. DOI 10.1056/NEJMoa2102953",
    },
    {
        "name": "mifflin_st_jeor",
        "title_ru": "Суточная потребность в энергии (Миффлин — Сан Жеор)",
        "title_en": "Mifflin-St Jeor energy expenditure",
        "area": "Диетология",
        "description": "Основной обмен по формуле Миффлина — Сан Жеора, умноженный на коэффициент активности.",
        "parameters": {
            "weight_kg": "масса тела, кг, 20–300",
            "height_cm": "рост, см, 100–250",
            "age": "возраст, лет, 18–120",
            "sex": "male / female",
            "activity": "sedentary / light / moderate / high / extreme",
        },
        "source": "Mifflin M.D. et al. Am J Clin Nutr, 1990. DOI 10.1093/ajcn/51.2.241",
    },
]


@mcp.tool(title="Список калькуляторов")
@value_errors_as_tool_errors
def list_calculators() -> dict[str, Any]:
    """Мета-инструмент: описание методик, допустимые границы и единицы измерения.

    Ключевые слова: список, справка, методики, catalog.
    """
    log.info("list_calculators")
    return {"server": "clinical-stdio", "calculators": CATALOG}


@mcp.tool(title="Индекс массы тела (ИМТ, BMI)")
@value_errors_as_tool_errors
def bmi(
    weight_kg: Annotated[float, Field(ge=20, le=300, description="Масса тела, кг")],
    height_cm: Annotated[float, Field(ge=100, le=250, description="Рост, см")],
) -> dict[str, Any]:
    """ИМТ (Body Mass Index): масса / рост² и класс по ВОЗ.

    Ключевые слова: вес, ожирение, масса тела, obesity, weight.
    """
    log.info("bmi weight=%s height=%s", weight_kg, height_cm)
    value = weight_kg / (height_cm / 100) ** 2
    if value < 18.5:
        risk, text = "yellow", "Недостаточная масса тела"
        recs = ["Обсудить с врачом причины низкой массы тела и рацион"]
    elif value < 25:
        risk, text = "green", "Нормальная масса тела"
        recs = ["Поддерживать текущий режим питания и активности"]
    elif value < 30:
        risk, text = "yellow", "Избыточная масса тела (предожирение)"
        recs = ["Увеличить физическую активность", "Оценить риск диабета по FINDRISC"]
    else:
        risk, text = "red", "Ожирение"
        recs = ["Консультация врача или эндокринолога", "Оценить риск диабета по FINDRISC"]
    return make_result(
        calculator="bmi",
        value=value,
        unit="кг/м²",
        risk=risk,
        interpretation=text,
        components={"Масса, кг": weight_kg, "Рост, м": round(height_cm / 100, 2)},
        recommendations=recs,
        source="WHO Technical Report Series 894, 2000",
        source_url="https://pubmed.ncbi.nlm.nih.gov/11234459/",
    )


@mcp.tool(title="FINDRISC — риск диабета 2 типа")
@value_errors_as_tool_errors
def findrisc(
    age: Annotated[int, Field(ge=18, le=120, description="Возраст, лет")],
    bmi: Annotated[float, Field(ge=10, le=80, description="ИМТ, кг/м²")],
    waist_cm: Annotated[float, Field(ge=40, le=200, description="Окружность талии, см")],
    sex: Annotated[Sex, Field(description="Пол")],
    physical_activity_daily: Annotated[bool, Field(description="Не менее 30 минут активности в день")] = True,
    vegetables_daily: Annotated[bool, Field(description="Овощи, фрукты или ягоды каждый день")] = True,
    antihypertensive_drugs: Annotated[bool, Field(description="Принимает препараты от давления")] = False,
    high_glucose_history: Annotated[bool, Field(description="Когда-либо находили повышенный сахар")] = False,
    family_history: Annotated[
        Literal["none", "second_degree", "first_degree"],
        Field(description="Диабет у родственников: нет / дальние / близкие"),
    ] = "none",
) -> dict[str, Any]:
    """FINDRISC (Finnish Diabetes Risk Score): риск диабета 2 типа на 10 лет, 0–26 баллов.

    Ключевые слова: диабет, сахар, профилактика, diabetes, prevention.
    """
    log.info("findrisc age=%s bmi=%s", age, bmi)
    parts: dict[str, int] = {}
    parts["Возраст"] = 0 if age < 45 else 2 if age < 55 else 3 if age < 65 else 4
    parts["ИМТ"] = 0 if bmi < 25 else 1 if bmi <= 30 else 3
    if sex == "male":
        parts["Талия"] = 0 if waist_cm < 94 else 3 if waist_cm <= 102 else 4
    else:
        parts["Талия"] = 0 if waist_cm < 80 else 3 if waist_cm <= 88 else 4
    parts["Мало активности"] = 0 if physical_activity_daily else 2
    parts["Мало овощей"] = 0 if vegetables_daily else 1
    parts["Препараты от давления"] = 2 if antihypertensive_drugs else 0
    parts["Повышенный сахар в анамнезе"] = 5 if high_glucose_history else 0
    parts["Наследственность"] = {"none": 0, "second_degree": 3, "first_degree": 5}[family_history]
    score = sum(parts.values())

    if score < 7:
        risk, text = "green", "Низкий риск: примерно 1 из 100 заболеет за 10 лет"
        recs = ["Поддерживать здоровый образ жизни"]
    elif score <= 11:
        risk, text = "yellow", "Слегка повышенный риск: примерно 1 из 25"
        recs = ["Больше движения и овощей", "Следить за массой тела"]
    elif score <= 14:
        risk, text = "yellow", "Умеренный риск: примерно 1 из 6"
        recs = ["Изменить питание и активность", "Обсудить с врачом анализ на глюкозу"]
    elif score <= 20:
        risk, text = "red", "Высокий риск: примерно 1 из 3"
        recs = ["Сдать анализ на глюкозу и HbA1c", "Консультация врача"]
    else:
        risk, text = "red", "Очень высокий риск: примерно 1 из 2"
        recs = ["Срочно обследоваться на диабет", "Консультация эндокринолога"]
    return make_result(
        calculator="findrisc",
        value=score,
        unit="баллов из 26",
        risk=risk,
        interpretation=text,
        components=parts,
        recommendations=recs,
        source="Lindström J., Tuomilehto J. Diabetes Care, 2003",
        source_url="https://doi.org/10.2337/diacare.26.3.725",
    )


@mcp.tool(title="СКФ по креатинину (CKD-EPI 2021, eGFR)")
@value_errors_as_tool_errors
def ckd_epi_2021(
    creatinine: Annotated[float, Field(gt=0, le=2000, description="Креатинин сыворотки")],
    age: Annotated[int, Field(ge=18, le=120, description="Возраст, лет")],
    sex: Annotated[Sex, Field(description="Пол")],
    creatinine_unit: Annotated[
        Literal["umol/L", "mg/dL"], Field(description="Единицы креатинина")
    ] = "umol/L",
) -> dict[str, Any]:
    """СКФ (eGFR) по формуле CKD-EPI 2021 без расового коэффициента, стадия по KDIGO.

    Ключевые слова: почки, нефрология, креатинин, СКФ, kidney, GFR.
    """
    log.info("ckd_epi_2021 creat=%s %s age=%s", creatinine, creatinine_unit, age)
    scr = creatinine / 88.4 if creatinine_unit == "umol/L" else creatinine
    if not 0.1 <= scr <= 20:
        # Значение вне физиологических границ: явная ошибка, а не тихий расчёт.
        raise ValueError(
            f"Креатинин {creatinine} {creatinine_unit} вне допустимых границ "
            "(0,1–20 мг/дл = 9–1768 мкмоль/л). Проверьте единицы измерения."
        )
    kappa, alpha, female = (0.7, -0.241, 1.012) if sex == "female" else (0.9, -0.302, 1.0)
    egfr = (
        142
        * min(scr / kappa, 1) ** alpha
        * max(scr / kappa, 1) ** -1.200
        * 0.9938**age
        * female
    )
    if egfr >= 90:
        stage, risk = "G1 — нормальная", "green"
    elif egfr >= 60:
        stage, risk = "G2 — незначительно снижена", "green"
    elif egfr >= 45:
        stage, risk = "G3a — умеренно снижена", "yellow"
    elif egfr >= 30:
        stage, risk = "G3b — существенно снижена", "yellow"
    elif egfr >= 15:
        stage, risk = "G4 — резко снижена", "red"
    else:
        stage, risk = "G5 — почечная недостаточность", "red"
    recs = {
        "green": ["Контроль раз в год при факторах риска (диабет, давление)"],
        "yellow": ["Наблюдение нефролога", "Коррекция доз препаратов, выводимых почками",
                   "Ограничение соли и белка — по назначению врача"],
        "red": ["Срочная консультация нефролога", "Пересмотр всех препаратов"],
    }[risk]
    return make_result(
        calculator="ckd_epi_2021",
        value=egfr,
        unit="мл/мин/1,73 м²",
        risk=risk,
        interpretation=f"Стадия {stage}",
        components={"Креатинин, мг/дл": round(scr, 2), "Возраст": age,
                    "κ": kappa, "α": alpha, "Коэффициент пола": female},
        recommendations=recs,
        source="Inker L.A. et al. NEJM, 2021",
        source_url="https://doi.org/10.1056/NEJMoa2102953",
    )


ACTIVITY = {
    "sedentary": (1.2, "сидячий образ жизни"),
    "light": (1.375, "лёгкая активность 1–3 раза в неделю"),
    "moderate": (1.55, "умеренная активность 3–5 раз в неделю"),
    "high": (1.725, "высокая активность 6–7 раз в неделю"),
    "extreme": (1.9, "очень высокая: спорт или физический труд"),
}


@mcp.tool(title="Суточная потребность в калориях (Миффлин — Сан Жеор)")
@value_errors_as_tool_errors
def mifflin_st_jeor(
    weight_kg: Annotated[float, Field(ge=20, le=300, description="Масса тела, кг")],
    height_cm: Annotated[float, Field(ge=100, le=250, description="Рост, см")],
    age: Annotated[int, Field(ge=18, le=120, description="Возраст, лет")],
    sex: Annotated[Sex, Field(description="Пол")],
    activity: Annotated[
        Literal["sedentary", "light", "moderate", "high", "extreme"],
        Field(description="Уровень физической активности"),
    ] = "sedentary",
) -> dict[str, Any]:
    """Mifflin-St Jeor: основной обмен и суточная потребность в энергии, ккал.

    Ключевые слова: калории, питание, диета, обмен веществ, calories, nutrition.
    """
    log.info("mifflin weight=%s height=%s age=%s", weight_kg, height_cm, age)
    bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age + (5 if sex == "male" else -161)
    factor, label = ACTIVITY[activity]
    total = bmr * factor
    return make_result(
        calculator="mifflin_st_jeor",
        value=round(total),
        unit="ккал/сутки",
        risk="info",
        interpretation=f"Основной обмен {round(bmr)} ккал × {factor} ({label})",
        components={"Основной обмен, ккал": round(bmr), "Коэффициент активности": factor},
        recommendations=[
            "Для снижения массы — дефицит 300–500 ккал от этого значения",
            "При хронических болезнях рацион согласовать с врачом",
        ],
        source="Mifflin M.D. et al. Am J Clin Nutr, 1990",
        source_url="https://doi.org/10.1093/ajcn/51.2.241",
    )


def main() -> None:
    log.info("Запуск stdio-сервера")
    mcp.run("stdio")


if __name__ == "__main__":
    main()
