"""Офлайн-замер среза kb без LLM: есть ли expected в срезе и длина среза — до и после узкого среза.

Запуск: python -m tools.eval_slice <каталог задач> [<каталог задач> ...]
"""
from __future__ import annotations

import asyncio
import json
import statistics
import sys
from pathlib import Path

import harness.snapshot as snapshot_mod
from harness.settings import load_settings_raw
from harness.snapshot import WorkspaceSnapshot
from harness.tasks import load_tasks


def measure(tasks_dir: Path) -> dict:
    snap = WorkspaceSnapshot(load_settings_raw()["snapshot"])
    real = snapshot_mod.kb_slice
    rows = []
    for task in load_tasks(tasks_dir):
        if task.task_type != "search":
            continue
        expected = json.loads((task.task_dir / "task.json").read_text(encoding="utf-8")).get("expected")
        snapshot_mod.kb_slice = lambda *a, **k: None
        old = asyncio.run(snap.build(task))
        snapshot_mod.kb_slice = real
        new = asyncio.run(snap.build(task))
        rows.append({"id": task.task_id, "expected": expected, "old": len(old), "new": len(new),
                     "old_hit": bool(expected) and expected in old, "new_hit": bool(expected) and expected in new})
    feasible = [r for r in rows if r["expected"]]
    return {
        "dir": str(tasks_dir), "search": len(rows), "feasible": len(feasible),
        "old_hit": sum(r["old_hit"] for r in feasible), "new_hit": sum(r["new_hit"] for r in feasible),
        "old_chars_median": statistics.median(r["old"] for r in rows) if rows else 0,
        "new_chars_median": statistics.median(r["new"] for r in rows) if rows else 0,
        "misses": [r["id"] for r in feasible if not r["new_hit"]],
    }


def main() -> None:
    for arg in sys.argv[1:]:
        print(json.dumps(measure(Path(arg)), ensure_ascii=False))


if __name__ == "__main__":
    main()
