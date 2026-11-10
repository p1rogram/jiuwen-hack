"""data_analysis для набора вариантов: CSV четырёх видов и вопросы новых видов; эталон считается по строкам CSV."""
from __future__ import annotations

import csv
import io
import math
import random
from collections import defaultdict
from typing import Any, Callable

from tools.gen.common import vocab
from tools.gen.content import variants_pool as pool

Rows = list[dict[str, Any]]
COLS = {
    "employees": ["id", "name", "department", "salary", "years"],
    "sales": ["order_id", "region", "product", "month", "units", "price"],
    "inventory": ["sku", "item", "category", "stock", "reorder_level", "unit_cost"],
    "grades": ["student", "subject", "score"],
}


def to_csv(cols: list[str], rows: Rows) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(cols)
    w.writerows([r[c] for c in cols] for r in rows)
    return buf.getvalue()


def rint(x: float) -> int | None:
    """Округление без спорной половины: x.5 у Python (банковское) и у человека разное — такой вариант не берём."""
    return None if abs(x - math.floor(x) - 0.5) < 1e-9 else round(x)


def unique_max(scores: dict[str, float]) -> str | None:
    best = max(scores.values())
    winners = [k for k, v in scores.items() if v == best]
    return winners[0] if len(winners) == 1 else None


def make_table(rng: random.Random, table: str) -> Rows:
    v = vocab()
    if table == "employees":
        names = rng.sample(v["first_names"], rng.choice([13, 15, 17, 19]))
        depts = rng.sample(pool.DEPARTMENTS, rng.randint(3, 5))
        return [{"id": i + 1, "name": n, "department": rng.choice(depts), "salary": rng.randrange(2500, 8000, 50),
                 "years": rng.randint(1, 20)} for i, n in enumerate(names)]
    if table == "sales":
        products = v["products_base"] + rng.sample(v["products_extra"], 2)
        return [{"order_id": i + 1, "region": rng.choice(v["regions"]), "product": rng.choice(products),
                 "month": rng.randint(1, 12), "units": rng.randint(1, 30), "price": rng.randint(5, 40)}
                for i in range(rng.randint(20, 60))]
    if table == "inventory":
        return [{"sku": i + 1, "item": item, "category": rng.choice(pool.CATEGORIES), "stock": rng.randint(0, 80),
                 "reorder_level": rng.randint(5, 40), "unit_cost": rng.randint(1, 60)}
                for i, item in enumerate(rng.sample(pool.ITEMS, rng.randint(10, 18)))]
    students = rng.sample(v["first_names"], rng.randint(5, 8))
    subjects = rng.sample(pool.SUBJECTS, rng.randint(3, 5))
    return [{"student": s, "subject": sub, "score": rng.randint(40, 100)} for s in students for sub in subjects]


def _by(rows: Rows, key: str, val: str) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for r in rows:
        out[r[key]] += r[val]
    return dict(out)


def _pick(rng: random.Random, rows: Rows, key: str) -> Any:
    return rng.choice(sorted({r[key] for r in rows}))


def q_employees(kind: str, rng: random.Random, rows: Rows) -> tuple[dict, Any]:
    dept = _pick(rng, rows, "department")
    in_dept = [r for r in rows if r["department"] == dept]
    if kind == "avg_salary_dept":
        return {"dept": dept}, rint(sum(r["salary"] for r in in_dept) / len(in_dept))
    if kind == "count_years_over":
        k = rng.randint(3, 12)
        return {"k": k}, sum(r["years"] > k for r in rows)
    if kind == "top_dept_total":
        return {}, unique_max(_by(rows, "department", "salary"))
    if kind == "top_earner_dept":
        return {"dept": dept}, unique_max({r["name"]: r["salary"] for r in in_dept})
    if kind == "median_salary":
        return {}, sorted(r["salary"] for r in rows)[len(rows) // 2]
    return {"dept": dept}, max(r["salary"] for r in in_dept) - min(r["salary"] for r in in_dept)


def q_sales(kind: str, rng: random.Random, rows: Rows) -> tuple[dict, Any]:
    product, region = _pick(rng, rows, "product"), _pick(rng, rows, "region")
    if kind == "max_units_product":
        return {"product": product}, max(r["units"] for r in rows if r["product"] == product)
    if kind == "revenue_region":
        return {"region": region}, sum(r["units"] * r["price"] for r in rows if r["region"] == region)
    if kind == "distinct_products_region":
        return {"region": region}, len({r["product"] for r in rows if r["region"] == region})
    if kind == "share_region":
        return {"region": region}, rint(100 * sum(r["region"] == region for r in rows) / len(rows))
    if kind == "top_month_units":
        return {}, unique_max(_by(rows, "month", "units"))
    m1 = rng.randint(1, 9)
    m2 = rng.randint(m1 + 1, 12)
    return ({"product": product, "m1": m1, "m2": m2},
            sum(r["units"] for r in rows if r["product"] == product and m1 <= r["month"] <= m2))


def q_inventory(kind: str, rng: random.Random, rows: Rows) -> tuple[dict, Any]:
    if kind == "below_reorder":
        return {}, sum(r["stock"] < r["reorder_level"] for r in rows)
    cat = _pick(rng, rows, "category")
    return {"category": cat}, sum(r["stock"] * r["unit_cost"] for r in rows if r["category"] == cat)


def q_grades(kind: str, rng: random.Random, rows: Rows) -> tuple[dict, Any]:
    if kind == "avg_score_student":
        student = _pick(rng, rows, "student")
        scores = [r["score"] for r in rows if r["student"] == student]
        return {"student": student}, rint(sum(scores) / len(scores))
    subject = _pick(rng, rows, "subject")
    return {"subject": subject}, unique_max({r["student"]: r["score"] for r in rows if r["subject"] == subject})


QUESTIONS: dict[str, tuple[str, Callable]] = {
    **{k: ("employees", q_employees) for k in ["avg_salary_dept", "count_years_over", "top_dept_total",
                                                "top_earner_dept", "median_salary", "salary_range_dept"]},
    **{k: ("sales", q_sales) for k in ["max_units_product", "revenue_region", "distinct_products_region",
                                        "share_region", "top_month_units", "units_months_product"]},
    **{k: ("inventory", q_inventory) for k in ["below_reorder", "category_value"]},
    **{k: ("grades", q_grades) for k in ["avg_score_student", "top_student_subject"]},
}


def instruction(rng: random.Random, table: str, cols: list[str], n: int, question: str) -> str:
    intro = rng.choice(pool.DATA_INTROS[table]).format(cols=",".join(cols), n=n)
    if intro.endswith(","):
        question = question[0].lower() + question[1:]
    hint = rng.choice(["", " Use bash with python3 to compute it."])
    return f"{intro} {question}{hint}"


def feasible(rng: random.Random, kind: str) -> tuple[str, dict[str, str], Any, str]:
    """(текст без хвоста, файлы, эталон, check); спорный эталон (ничья, x.5) — новая таблица."""
    table, ask = QUESTIONS[kind]
    while True:
        rows = make_table(rng, table)
        params, answer = ask(kind, rng, rows)
        if answer is not None:
            break
    text = instruction(rng, table, COLS[table], len(rows), pool.DATA_Q[kind].format(**params))
    check = "exact_int" if isinstance(answer, int) else "exact_str"
    return text, {f"{table}.csv": to_csv(COLS[table], rows)}, str(answer), check


def infeasible(rng: random.Random, spec: dict[str, str]) -> tuple[str, dict[str, str]]:
    """Объявленный в условии столбец, которого нет в CSV."""
    table = spec["table"]
    rows = make_table(rng, table)
    params = {"dept": _pick(rng, rows, "department")} if table == "employees" else {"region": _pick(rng, rows, "region")}
    text = instruction(rng, table, COLS[table] + [spec["declared"]], len(rows), spec["question"].format(**params))
    return text, {f"{table}.csv": to_csv(COLS[table], rows)}
