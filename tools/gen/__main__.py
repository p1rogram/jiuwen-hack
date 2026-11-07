"""Генерация тренировочного набора задач в формате примеров организаторов.

Запуск из корня репо: python -m tools.gen --out D:/claude-tmp/jiuwen-runs/gen1 --n 60 --seed 1 --infeasible 0.2
Разметка (выполнима ли задача, вид, ожидаемый ответ) — в <out>/_labels.json; харнесс её не читает.
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from tools.gen import coding, data_analysis, math_task, search
from tools.gen.common import write_task

GENERATORS = {"coding": coding, "data_analysis": data_analysis, "math": math_task, "search": search}


def build_plan(rng: random.Random, n: int, types: list[str], infeasible: float, hard: float) -> list[tuple]:
    """Список (тип, вариант, вид невыполнимости) длины n; виды каждого типа идут по кругу."""
    kinds_queue = {t: [] for t in types}
    type_list = [types[i % len(types)] for i in range(n)]
    rng.shuffle(type_list)
    infeasible_idx = set(rng.sample(range(n), round(n * infeasible)))
    plan = []
    for i, ttype in enumerate(type_list):
        if i in infeasible_idx:
            if not kinds_queue[ttype]:
                kinds_queue[ttype] = sorted(GENERATORS[ttype].KINDS)
                rng.shuffle(kinds_queue[ttype])
            plan.append((ttype, "infeasible", kinds_queue[ttype].pop()))
        else:
            plan.append((ttype, "hard" if rng.random() < hard else "base", None))
    return plan


def generate(out: Path, n: int, seed: int, infeasible: float, hard: float, types: list[str]) -> dict:
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"{out} is not empty")
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    labels = {}
    for idx, (ttype, variant, kind) in enumerate(build_plan(rng, n, types, infeasible, hard)):
        task = GENERATORS[ttype].make(rng, variant, kind)
        task_id = f"{ttype}_{idx:04d}"
        labels[task_id] = write_task(out, task_id, task, rng.randint(1, 5))
    (out / "_labels.json").write_text(json.dumps(labels, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return labels


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--n", type=int, default=60)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--infeasible", type=float, default=0.2, help="доля невыполнимых")
    p.add_argument("--hard", type=float, default=0.5, help="доля усложнённых среди выполнимых")
    p.add_argument("--types", default=",".join(GENERATORS))
    a = p.parse_args()
    labels = generate(a.out, a.n, a.seed, a.infeasible, a.hard, a.types.split(","))
    bad = sum(1 for x in labels.values() if not x["feasible"])
    print(f"{len(labels)} tasks, {bad} infeasible -> {a.out}")


if __name__ == "__main__":
    main()
