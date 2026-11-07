"""Генератор data_analysis-задач: sales.csv + вопрос о сумме units (или выручке) по продукту и месяцам."""
from __future__ import annotations

import random

from tools.gen.common import GenTask, templates, vocab

COLUMNS = ["order_id", "region", "product", "month", "units", "price"]
KINDS = {
    "missing_file": "hard",     # sales.csv нет
    "missing_column": "hard",   # нет столбца, без которого вопрос не посчитать
    "unknown_product": "soft",  # продукта нет в CSV (ответ «0» спорен)
    "bad_months": "soft",       # месяцы вне 1..12
}
NEEDED = {"units": ["units"], "units_region": ["units", "region"], "revenue": ["units", "price"]}


def answer(rows: list[list], q: str, product: str, region: str, m1: int, m2: int) -> int:
    total = 0
    for _, reg, prod, month, units, price in rows:
        if prod != product or not (m1 <= month <= m2):
            continue
        if q == "units_region" and reg != region:
            continue
        total += units * price if q == "revenue" else units
    return total


def render_csv(rows: list[list], drop: str | None) -> str:
    keep = [i for i, c in enumerate(COLUMNS) if c != drop]
    lines = [",".join(COLUMNS[i] for i in keep)]
    lines += [",".join(str(r[i]) for i in keep) for r in rows]
    return "\n".join(lines) + "\n"


def make(rng: random.Random, variant: str, kind: str | None = None) -> GenTask:
    v, t = vocab(), templates()
    big = variant == "hard" or (kind is not None and rng.random() < 0.5)
    n_rows = rng.randint(1000, 5000) if big else rng.choice([20, 50, 120, 300])
    products = v["products_base"] + (rng.sample(v["products_extra"], rng.randint(2, 4)) if big else [])
    rows = [[i, rng.choice(v["regions"]), rng.choice(products), rng.randint(1, 12), rng.randint(1, 40),
             rng.randint(5, 99)] for i in range(1, n_rows + 1)]
    q = rng.choice(["units_region", "revenue"]) if big else "units"

    for _ in range(200):  # выполнимая задача с ненулевым ответом
        product, region = rng.choice(products), rng.choice(v["regions"])
        m1 = rng.randint(1, 12)
        m2 = min(12, m1 + rng.randint(0, 3))
        expected = answer(rows, q, product, region, m1, m2)
        if expected > 0:
            break
    drop = None
    if kind == "unknown_product":
        product = rng.choice(v["products_absent"])
    elif kind == "bad_months":
        m1 = rng.randint(13, 15)
        m2 = m1 + rng.randint(0, 2)
    elif kind == "missing_column":
        drop = rng.choice(NEEDED[q])

    question = t["data"][q].format(product=product, region=region, m1=m1, m2=m2)
    instruction = " ".join([t["data"]["intro"].format(rows=n_rows), question, t["data"]["tool"], t["answer_suffix"]])
    files = {} if kind == "missing_file" else {"sales.csv": render_csv(rows, drop)}
    task = GenTask("data_analysis", instruction, "exact_int", str(expected), files, variant)
    if kind is not None:
        task.kind, task.severity, task.expected = kind, KINDS[kind], None
    return task
