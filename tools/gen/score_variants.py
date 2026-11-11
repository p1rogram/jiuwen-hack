"""Оценка прогона набора вариантов: python -m tools.gen.score_variants <tasks-dir> <run.jsonl> [--json out.json]

Ответ — <task>/answer.txt, сравнение strip + lower (так сравнивают организаторы); coding — видимые тесты в
workspace и скрытый тест из <tasks-dir>-hidden; невыполнимые в счёт решённых не входят.
"""
from __future__ import annotations

import argparse
import json
import shutil
import asyncio
import logging
import tempfile
from collections import defaultdict
from pathlib import Path

from harness.test_runner import run_tests


def tests_pass(ws: Path, hidden: Path | None, *, require_hidden: bool = False, timeout_sec: float = 60) -> tuple[bool, bool]:
    """(видимые, скрытые) на копии workspace: проверка не должна менять папку задачи."""
    tmp = Path(tempfile.mkdtemp())
    try:
        work = tmp / "ws"
        shutil.copytree(ws, work)

        def ok(pattern: str) -> bool:
            result = asyncio.run(run_tests(work, pattern, timeout_sec))
            if not result.ok:
                logging.getLogger(__name__).debug("%s: %s", pattern, result.stderr)
            return result.ok

        visible = ok("test_*.py")
        if hidden is None or not hidden.exists():
            return visible, not require_hidden
        shutil.copy(hidden, work / "_holdout_hidden_test.py")
        return visible, ok("_holdout_hidden_test.py")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def read_log(log: Path) -> dict[str, dict]:
    tasks = {}
    for line in log.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec.get("kind") == "task":
            tasks[rec["task_id"]] = rec
    return tasks


def verdict(root: Path, task_id: str, exp: dict) -> tuple[str, str | None]:
    ans_file = root / task_id / "answer.txt"
    answer = ans_file.read_text(encoding="utf-8").strip() if ans_file.exists() else None
    if not exp["feasible"]:
        return ("SKIP-OK" if answer is None else "ANSWERED-INFEASIBLE"), answer
    if exp["type"] == "coding":
        hidden = root.parent / f"{root.name}-hidden" / task_id / "test_hidden.py"
        visible, hid = tests_pass(root / task_id / "workspace", hidden, require_hidden=exp.get("require_hidden", False))
        return ("OK" if visible and hid else "VISIBLE-ONLY" if visible else "FAIL"), answer
    ok = answer is not None and answer.lower() == str(exp["expected"]).strip().lower()
    return ("OK" if ok else "WRONG"), answer


def score(root: Path, log: Path) -> dict:
    expected = json.loads((root.parent / f"{root.name}-expected.json").read_text(encoding="utf-8"))
    recs = read_log(log)
    rows, by_type = {}, defaultdict(lambda: {"ok": 0, "feasible": 0, "cost": 0.0})
    for task_id, exp in sorted(expected.items()):
        rec = recs.get(task_id, {})
        v, answer = verdict(root, task_id, exp)
        cost = float(rec.get("cost") or 0)
        rows[task_id] = {"verdict": v, "status": rec.get("status"), "cost": cost, "answer": answer,
                         "expected": exp["expected"], "signals": [s["code"] + ":" + s["severity"]
                                                                  for s in rec.get("signals", [])]}
        t = by_type[exp["type"]]
        t["ok"] += v == "OK"
        t["feasible"] += exp["feasible"]
        t["cost"] += cost
    total = {"ok": sum(t["ok"] for t in by_type.values()), "feasible": sum(t["feasible"] for t in by_type.values()),
             "cost": round(sum(t["cost"] for t in by_type.values()), 1)}
    return {"tasks": rows, "by_type": dict(by_type), "total": total}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("tasks_dir", type=Path)
    p.add_argument("log", type=Path)
    p.add_argument("--json", type=Path, default=None)
    args = p.parse_args()
    result = score(args.tasks_dir, args.log)
    for task_id, r in result["tasks"].items():
        print(f"{r['verdict']:20} {task_id:20} status={r['status']!s:18} cost={r['cost']:7.1f} "
              f"ans={r['answer']!r} exp={r['expected']!r} sig={r['signals']}")
    for ttype, t in sorted(result["by_type"].items()):
        print(f"{ttype:14} {t['ok']}/{t['feasible']}  cost {t['cost']:.1f}")
    tot = result["total"]
    print(f"OK {tot['ok']}/{tot['feasible']} feasible, total cost {tot['cost']}")
    if args.json:
        args.json.write_bytes(json.dumps(result, ensure_ascii=False, indent=1).encode("utf-8"))


if __name__ == "__main__":
    main()
