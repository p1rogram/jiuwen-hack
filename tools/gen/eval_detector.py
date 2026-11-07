"""Офлайн-оценка детектора невыполнимых по разметке набора (без LLM).

Запуск: python -m tools.gen.eval_detector <set-dir> [--show-fp] [--show-fn]
Положительный класс — невыполнимая задача. Две политики: пропуск только по hard и по hard+soft.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from harness.feasibility import FeasibilityDetector, blocking
from harness.settings import load_config
from harness.tasks import load_tasks

POLICIES = {"hard": ["hard"], "hard+soft": ["hard", "soft"]}


def evaluate(set_dir: Path) -> dict:
    labels = json.loads((set_dir / "_labels.json").read_text(encoding="utf-8"))
    detector = FeasibilityDetector(load_config("feasibility.json"))
    rows = []
    for task in load_tasks(set_dir):
        signals = detector.check(task)
        lab = labels[task.task_id]
        rows.append({"task_id": task.task_id, "type": task.task_type, "feasible": lab["feasible"],
                     "kind": lab["kind"], "reward": lab["reward"], "signals": [s.as_dict() for s in signals],
                     "flag": {p: bool(blocking(signals, sev)) for p, sev in POLICIES.items()}})
    summary = {}
    for policy in POLICIES:
        c = Counter(tp=0, fp=0, fn=0, tn=0)
        for r in rows:
            c[("tp" if r["flag"][policy] else "fn") if not r["feasible"] else ("fp" if r["flag"][policy] else "tn")] += 1
        prec = c["tp"] / (c["tp"] + c["fp"]) if c["tp"] + c["fp"] else 1.0
        rec = c["tp"] / (c["tp"] + c["fn"]) if c["tp"] + c["fn"] else 1.0
        lost = sum(r["reward"] for r in rows if r["feasible"] and r["flag"][policy])
        summary[policy] = {**c, "precision": round(prec, 3), "recall": round(rec, 3), "reward_lost": lost}
    by_kind: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        if not r["feasible"]:
            by_kind[f"{r['type']}/{r['kind']}"]["n"] += 1
            by_kind[f"{r['type']}/{r['kind']}"]["hard"] += r["flag"]["hard"]
            by_kind[f"{r['type']}/{r['kind']}"]["hard+soft"] += r["flag"]["hard+soft"]
    return {"summary": summary, "by_kind": {k: dict(v) for k, v in sorted(by_kind.items())}, "rows": rows}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("set_dir", type=Path)
    p.add_argument("--show-fp", action="store_true")
    p.add_argument("--show-fn", action="store_true")
    a = p.parse_args()
    res = evaluate(a.set_dir)
    for policy, s in res["summary"].items():
        print(f"{policy:<10} tp={s['tp']} fp={s['fp']} fn={s['fn']} tn={s['tn']} "
              f"precision={s['precision']} recall={s['recall']} reward_lost={s['reward_lost']}")
    print("\nkind                              n  hard  hard+soft")
    for kind, c in res["by_kind"].items():
        print(f"{kind:<32} {c['n']:>3} {c.get('hard', 0):>5} {c.get('hard+soft', 0):>10}")
    for r in res["rows"]:
        fp = r["feasible"] and r["flag"]["hard+soft"]
        fn = not r["feasible"] and not r["flag"]["hard"]
        if (a.show_fp and fp) or (a.show_fn and fn):
            print(f"{'FP' if fp else 'FN'} {r['task_id']} {r['kind']} {r['signals']}")


if __name__ == "__main__":
    main()
