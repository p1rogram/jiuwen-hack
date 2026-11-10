"""Офлайн-оценка решателя по правилам: сколько задач он берёт и сколько ответов неверно (без LLM).

Эталон — `expected` из task.json (примеры организаторов) или из `_labels.json` генератора.
Запуск: python -m tools.gen.eval_rules <набор> [<набор> ...] [--show]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harness.rules import RulesSolver  # noqa: E402
from harness.settings import load_config  # noqa: E402
from harness.tasks import load_tasks  # noqa: E402


def truth(set_dir: Path) -> dict[str, dict]:
    labels_path = set_dir / "_labels.json"
    if labels_path.exists():
        return json.loads(labels_path.read_text(encoding="utf-8"))
    out = {}
    for tj in set_dir.glob("*/task.json"):
        d = json.loads(tj.read_text(encoding="utf-8"))
        out[tj.parent.name] = {"feasible": True, "expected": d.get("expected")}
    return out


def evaluate(set_dir: Path, solver: RulesSolver) -> tuple[dict[str, Counter], list[str]]:
    labels = truth(set_dir)
    by_type: dict[str, Counter] = defaultdict(Counter)
    notes = []
    for task in load_tasks(set_dir):
        if task.task_type == "coding":
            continue
        lab = labels.get(task.task_id, {"feasible": True, "expected": None})
        c = by_type[task.task_type]
        c["feasible" if lab["feasible"] else "infeasible"] += 1
        answer = solver.solve(task)
        if answer is None:
            if lab["feasible"]:
                notes.append(f"MISS {task.task_id}")
            continue
        if not lab["feasible"]:
            c["taken_infeasible"] += 1
            notes.append(f"TAKEN-INFEASIBLE {task.task_id} {lab.get('kind')} -> {answer!r}")
        elif str(lab["expected"]) == answer:
            c["right"] += 1
        else:
            c["wrong"] += 1
            notes.append(f"WRONG {task.task_id}: {answer!r} != {lab['expected']!r}")
    return by_type, notes


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("sets", type=Path, nargs="+")
    p.add_argument("--show", action="store_true")
    a = p.parse_args()
    solver = RulesSolver(load_config("rules.json"))
    for set_dir in a.sets:
        by_type, notes = evaluate(set_dir, solver)
        print(set_dir.name)
        for t, c in sorted(by_type.items()):
            print(f"  {t:<14} feasible {c['feasible']:>3}  right {c['right']:>3}  wrong {c['wrong']}  "
                  f"infeasible {c['infeasible']:>2}  taken_infeasible {c['taken_infeasible']}")
        for n in notes:
            if a.show or not n.startswith("MISS"):
                print("   ", n)


if __name__ == "__main__":
    main()
