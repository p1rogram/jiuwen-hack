"""Генератор math-задач: цепочка операций со складом, как в примерах организаторов."""
from __future__ import annotations

import random

from tools.gen.common import GenTask, templates, vocab

KINDS = {
    "missing_start": "hard",    # «starts with an unknown number of boxes»
    "vague_quantity": "hard",   # число заменено словом «some/several/a few»
    "div_zero": "hard",         # «split equally into 0 warehouses»
    "unknown_unit": "soft",     # вопрос о величине, которой нет в условии
}
MIN_STOCK = 20


def next_op(rng: random.Random, stock: int, ops: list[str]) -> tuple[str, dict[str, int], int]:
    op = rng.choice(ops)
    if op == "ship":
        p = {"x": rng.randint(10, 60), "d": rng.randint(2, 5)}
        new = stock - p["x"] * p["d"]
    elif op == "sell":
        p = {"x": rng.randint(10, 200)}
        new = stock - p["x"]
    elif op == "split":
        p = {"n": rng.randint(2, 4)}
        new = stock // p["n"]
    elif op == "double":
        p = {}
        new = stock * 2
    else:
        op, p = "receive", {"k": rng.randint(2, 4), "m": rng.randint(20, 120)}
        new = stock + p["k"] * p["m"]
    if new < MIN_STOCK or new > 200_000:
        p = {"k": rng.randint(2, 4), "m": rng.randint(20, 120)}
        return "receive", p, stock + p["k"] * p["m"]
    return op, p, new


def make(rng: random.Random, variant: str, kind: str | None = None) -> GenTask:
    t, v = templates()["math"], vocab()
    big = variant == "hard" or (kind is not None and rng.random() < 0.5)
    allowed = ["ship", "receive", "split", "double"] + (["sell"] if big else [])
    stock = rng.randint(1000, 9999) if big else rng.randint(500, 2000)
    start = stock
    steps: list[tuple[str, dict]] = []
    for _ in range(rng.randint(6, 10) if big else rng.randint(2, 5)):
        op, params, stock = next_op(rng, stock, allowed)
        steps.append((op, dict(params)))

    start_text = t["start"].format(n=start)
    unit = "boxes"
    if kind == "missing_start":
        start_text = t["start_unknown"]
    elif kind == "vague_quantity":
        numbered = [s for s in steps if s[0] in ("ship", "receive", "sell")]
        if not numbered:
            steps.append(("ship", {"x": 10, "d": 2}))
            numbered = steps[-1:]
        _, params = rng.choice(numbered)
        params[rng.choice(sorted(params))] = rng.choice(v["vague_words"])
    elif kind == "div_zero":
        splits = [s for s in steps if s[0] == "split"]
        if splits:
            rng.choice(splits)[1]["n"] = 0
        else:
            steps.insert(rng.randint(0, len(steps)), ("split", {"n": 0}))
    elif kind == "unknown_unit":
        unit = rng.choice(v["math_units_absent"])

    body = [start_text] + [t[op].format(**params) for op, params in steps]
    instruction = " ".join(body + [t["question"].format(unit=unit), t["tool"], templates()["answer_suffix"]])
    task = GenTask("math", instruction, "exact_int", str(stock), {}, variant, has_workspace=False)
    if kind is not None:
        task.kind, task.severity, task.expected = kind, KINDS[kind], None
    return task
