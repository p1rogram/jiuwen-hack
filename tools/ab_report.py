"""Парный отчёт A/B по прогонам на одних задачах: решено, с первой попытки, цена, выход stage1.1, форма ответа.

Запуск: python -m tools.ab_report --run A <tasks-dir> <log.jsonl> <prefix> [--run A ...] --run B ... [--json out.json]
Плечо — одно или несколько прогонов; ключ задачи — prefix + имя папки (prefix «-» — без префикса), поздний прогон
плеча перекрывает ранний по ключу. Сравниваются только задачи, которые есть во всех плечах; первое плечо — база.
Вид задачи — `variant` из `_labels.json` (ключ — имя папки или task_id; у генератора coding base — 2–3 бага,
hard — 4–7), без разметки — example.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

from tools.eval_answers import judge_labeled, load_labels

SHAPE_KEYS = ("before_chars", "after_chars", "changed", "unchanged", "defs")


def read_log(path: Path) -> dict[str, dict[str, Any]]:
    """task_id → итог задачи, сумма цены, выход stage1.1, форма ответа stage1.1."""
    acc: dict[str, dict[str, Any]] = defaultdict(lambda: {"task": {}, "cost": 0.0, "out11": 0, "shape": None})
    for line in path.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        kind, tid = rec.get("kind"), rec.get("task_id")
        if kind == "task":
            acc[tid]["task"] = rec
        elif kind == "llm_call":
            acc[tid]["cost"] += rec["cost"]
            if rec.get("stage") == "stage1.1":
                acc[tid]["out11"] += rec["output_tokens"]
        elif kind == "answer_shape" and rec.get("stage") == "stage1.1":
            acc[tid]["shape"] = rec
    return acc


def read_run(tasks_dir: Path, log: Path, prefix: str) -> dict[str, dict[str, Any]]:
    labels = load_labels(tasks_dir)
    calls = read_log(log)
    rows: dict[str, dict[str, Any]] = {}
    seen: dict[str, str] = {}
    for tj in sorted(tasks_dir.glob("*/task.json")):
        data = json.loads(tj.read_text(encoding="utf-8"))
        tid, folder = data["task_id"], tj.parent.name
        if tid in seen:
            raise SystemExit(f"{tasks_dir}: task_id {tid} in {seen[tid]} and {folder} — журнал их не различит")
        seen[tid] = folder
        if tid not in calls:
            continue  # задачу в этом прогоне не решали (--only, обрыв)
        c = calls[tid]
        label = labels.get(folder) or labels.get(tid) or {}
        ok, _ = judge_labeled(tj.parent, data, {tid: label} if label else {})
        task = c["task"]
        rows[("" if prefix == "-" else prefix) + folder] = {
            "ok": ok, "first_try": ok and task.get("solved_by") == "stage1" and task.get("attempts") == 1,
            "cost": c["cost"], "out11": c["out11"], "shape": c["shape"], "variant": label.get("variant"),
            "feasible": label.get("feasible", True),
        }
    return rows


def merge_run(arm: dict[str, dict[str, Any]], run: dict[str, dict[str, Any]]) -> None:
    """Поздний прогон перекрывает ранний; разметку без `_labels.json` (перепрогон части задач) берём у раннего."""
    for key, row in run.items():
        prev = arm.get(key)
        if prev is not None and row["variant"] is None:
            row = {**row, "variant": prev["variant"], "feasible": prev["feasible"]}
        arm[key] = row


def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    shapes = [r["shape"] for r in rows if r["shape"]]
    out: dict[str, Any] = {
        "tasks": len(rows), "feasible": sum(r["feasible"] for r in rows), "solved": sum(r["ok"] for r in rows), "first_try": sum(r["first_try"] for r in rows),
        "cost": round(sum(r["cost"] for r in rows), 1), "out11_mean": round(mean(r["out11"] for r in rows), 1)
        if rows else 0.0,
    }
    if shapes:
        out["shape"] = {k: round(mean(s.get(k, 0) for s in shapes), 1) for k in SHAPE_KEYS}
        out["shape"]["whole_file_share"] = round(sum(bool(s.get("whole_file")) for s in shapes) / len(shapes), 2)
        applied: dict[str, int] = defaultdict(int)
        for s in shapes:
            applied[s.get("applied", "?")] += 1
        out["shape"]["applied"] = dict(sorted(applied.items()))
    return out


def build(arms: dict[str, dict[str, dict[str, Any]]]) -> dict[str, Any]:
    names = list(arms)
    keys = sorted(set.intersection(*(set(a) for a in arms.values()))) if arms else []
    base = arms[names[0]] if names else {}
    report: dict[str, Any] = {"paired": len(keys), "arms": {}}
    for name in names:
        rows = [arms[name][k] for k in keys]
        by_variant: dict[str, list] = defaultdict(list)
        for r in rows:
            by_variant[r["variant"] or "example"].append(r)
        entry = summary(rows)
        entry["by_variant"] = {v: summary(rs) for v, rs in sorted(by_variant.items())}
        if name != names[0]:
            entry["cost_vs_base"] = round(entry["cost"] / report["arms"][names[0]]["cost"] - 1, 3) \
                if report["arms"][names[0]]["cost"] else None
            entry["regressions"] = [k for k in keys if base[k]["ok"] and not arms[name][k]["ok"]]
            entry["improvements"] = [k for k in keys if not base[k]["ok"] and arms[name][k]["ok"]]
        report["arms"][name] = entry
    return report


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--run", nargs=4, action="append", required=True, metavar=("ARM", "TASKS_DIR", "LOG", "PREFIX"))
    p.add_argument("--json", type=Path, default=None)
    a = p.parse_args(argv)
    arms: dict[str, dict[str, dict[str, Any]]] = {}
    for arm, tasks_dir, log, prefix in a.run:
        merge_run(arms.setdefault(arm, {}), read_run(Path(tasks_dir), Path(log), prefix))
    rep = build(arms)
    print(f"paired tasks: {rep['paired']}")
    for name, e in rep["arms"].items():
        line = (f"{name}: solved {e['solved']}/{e['feasible']} feasible ({e['tasks']} tasks), first try {e['first_try']}, cost {e['cost']:.0f}"
                f", stage1.1 out {e['out11_mean']}")
        if "cost_vs_base" in e:
            line += f", cost vs base {e['cost_vs_base']:+.1%}, regressions {len(e['regressions'])}" \
                    f", improvements {len(e['improvements'])}"
        print(line)
        for v, s in e["by_variant"].items():
            print(f"  {v:<10} solved {s['solved']}/{s['feasible']} ({s['tasks']}), first try {s['first_try']}, cost {s['cost']:.0f}")
        if "shape" in e:
            print(f"  shape {json.dumps(e['shape'], ensure_ascii=False)}")
        for k in e.get("regressions", []):
            print(f"  REGRESSION {k}")
    if a.json:
        a.json.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
