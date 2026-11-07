"""Отчёт прогона харнесса: баллы, токены, работа детектора невыполнимых — по ответам в наборе и журналу.

Запуск: python -m tools.gen.report <tasks-dir> <logs/run-*.jsonl>
Без _labels.json (примеры организаторов) все задачи считаются выполнимыми.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from tools.eval_answers import judge_labeled, load_labels


def read_log(path: Path) -> tuple[dict[str, dict], dict[str, dict]]:
    tasks: dict[str, dict] = {}
    calls: dict[str, dict] = defaultdict(lambda: {"cost": 0.0, "in": 0, "out": 0, "n": 0, "s2_cost": 0.0, "s2_n": 0})
    for line in path.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec.get("kind") == "task":
            tasks[rec["task_id"]] = rec
        elif rec.get("kind") == "llm_call":
            c = calls[rec["task_id"]]
            c["cost"] += rec["cost"]
            c["in"] += rec["input_tokens"]
            c["out"] += rec["output_tokens"]
            c["n"] += 1
            if str(rec.get("stage", "")).startswith("stage2."):
                c["s2_cost"] += rec["cost"]
                c["s2_n"] += 1
    return tasks, calls


def build(root: Path, log: Path) -> dict:
    labels = load_labels(root)
    tasks, calls = read_log(log)
    rows = []
    for tj in sorted(root.glob("*/task.json")):
        data = json.loads(tj.read_text(encoding="utf-8"))
        tid = data["task_id"]
        lab = labels.get(tid, {"feasible": True, "variant": "example", "kind": "ok"})
        ok, answer = judge_labeled(tj.parent, data, labels)
        rec = tasks.get(tid, {})
        rows.append({"task_id": tid, "type": data["task_type"], "reward": data["reward"], "feasible": lab["feasible"],
                     "variant": lab["variant"], "kind": lab["kind"], "status": rec.get("status", "missing"),
                     "ok": ok, "answer": answer, "solved_by": rec.get("solved_by", ""),
                     "stage2_stop": rec.get("stage2_stop", ""),
                     **calls.get(tid, {"cost": 0.0, "in": 0, "out": 0, "n": 0, "s2_cost": 0.0, "s2_n": 0})})
    skipped = lambda r: r["status"] == "skipped_infeasible"  # noqa: E731
    tp = sum(1 for r in rows if not r["feasible"] and skipped(r))
    fp = sum(1 for r in rows if r["feasible"] and skipped(r))
    fn = sum(1 for r in rows if not r["feasible"] and not skipped(r))
    return {
        "tasks": len(rows), "infeasible": sum(not r["feasible"] for r in rows),
        "reward": sum(r["reward"] for r in rows if r["ok"]),
        "reward_feasible": sum(r["reward"] for r in rows if r["feasible"]),
        "solved": sum(r["ok"] for r in rows), "feasible": sum(r["feasible"] for r in rows),
        "cost": round(sum(r["cost"] for r in rows), 1),
        "output_tokens": sum(r["out"] for r in rows), "input_tokens": sum(r["in"] for r in rows),
        "llm_calls": sum(r["n"] for r in rows),
        "cost_on_infeasible": round(sum(r["cost"] for r in rows if not r["feasible"]), 1),
        "detector": {"tp": tp, "fp": fp, "fn": fn,
                     "precision": round(tp / (tp + fp), 3) if tp + fp else None,
                     "recall": round(tp / (tp + fn), 3) if tp + fn else None,
                     "reward_lost": sum(r["reward"] for r in rows if r["feasible"] and skipped(r))},
        "stage2": {"tasks": sum(r["s2_n"] > 0 for r in rows), "calls": sum(r["s2_n"] for r in rows),
                   "cost": round(sum(r["s2_cost"] for r in rows), 1),
                   "rescued": sum(r["ok"] and r["solved_by"] == "stage2" for r in rows),
                   "rescued_reward": sum(r["reward"] for r in rows if r["ok"] and r["solved_by"] == "stage2"),
                   "skipped_low_value": sum(r["status"] == "skipped_low_value" for r in rows)},
        "rows": rows,
    }


def group(rows: list[dict], key: str) -> list[str]:
    acc: dict[str, list] = defaultdict(list)
    for r in rows:
        if r["feasible"]:
            acc[r[key]].append(r)
    lines = []
    for k, rs in sorted(acc.items()):
        cost = sum(r["cost"] for r in rs)
        lines.append(f"  {k:<14} solved {sum(r['ok'] for r in rs)}/{len(rs)}  reward {sum(r['reward'] for r in rs if r['ok'])}"
                     f"/{sum(r['reward'] for r in rs)}  cost {cost:.0f} (avg {cost / len(rs):.0f})")
    return lines


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("tasks_dir", type=Path)
    p.add_argument("log", type=Path)
    p.add_argument("--json", type=Path, default=None, help="сохранить отчёт целиком")
    a = p.parse_args()
    rep = build(a.tasks_dir, a.log)
    d = rep["detector"]
    print(f"tasks {rep['tasks']} (infeasible {rep['infeasible']}), solved {rep['solved']}/{rep['feasible']} feasible")
    print(f"reward {rep['reward']} / {rep['reward_feasible']} achievable")
    print(f"cost {rep['cost']} out-tokens-equivalent: output {rep['output_tokens']}, input {rep['input_tokens']}, "
          f"calls {rep['llm_calls']}; spent on infeasible {rep['cost_on_infeasible']}")
    print(f"detector in run: tp={d['tp']} fp={d['fp']} fn={d['fn']} precision={d['precision']} "
          f"recall={d['recall']} reward_lost={d['reward_lost']}")
    s2 = rep["stage2"]
    print(f"stage2: {s2['tasks']} tasks, {s2['calls']} calls, cost {s2['cost']}; rescued {s2['rescued']} "
          f"(reward {s2['rescued_reward']}); skipped_low_value {s2['skipped_low_value']}")
    print("by type:", *group(rep["rows"], "type"), sep="\n")
    print("by variant:", *group(rep["rows"], "variant"), sep="\n")
    for r in rep["rows"]:
        if r["feasible"] and not r["ok"]:
            print(f"FAILED {r['task_id']} {r['variant']} status={r['status']} answer={r['answer']!r} cost={r['cost']:.0f}"
                  + (f" stage2={r['s2_cost']:.0f} stop={r['stage2_stop']}" if r["s2_n"] else ""))
        if r["ok"] and r["solved_by"] == "stage2":
            print(f"RESCUED {r['task_id']} {r['variant']} stage2={r['s2_cost']:.0f} in {r['s2_n']} calls")
        if not r["feasible"] and r["status"] != "skipped_infeasible":
            print(f"ATTEMPTED-INFEASIBLE {r['task_id']} {r['kind']} status={r['status']} cost={r['cost']:.0f}")
    if a.json:
        a.json.write_text(json.dumps(rep, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
