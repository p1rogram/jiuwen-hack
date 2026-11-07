"""Наблюдения расхода по задачам из журналов прогона (logs/run-*.jsonl) + набора задач, на котором шёл прогон.

Пара задаётся как <tasks-dir>=<log>. Набор нужен исходный (до прогона): если в журнале нет prompt_chars
(журналы до модели расхода), промпт этапа 1 пересобирается — у coding срез включает вывод тестов, а после
прогона mylib.py уже исправлен.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

from harness.prompting import Stage1Prompt
from harness.settings import load_settings_raw
from harness.snapshot import WorkspaceSnapshot
from harness.tasks import Task, load_tasks

# порядок старого планировщика (cost_estimate_by_type до модели расхода) — база для сравнения
OLD_COSTS = {"coding": 1200, "data_analysis": 400, "math": 300, "search": 300, "default": 1000}


@dataclass
class Obs:
    run: str
    task_id: str
    task_type: str
    reward: float
    status: str
    feasible: bool
    cost: float = 0.0
    prompt_chars: int = 0
    in1: int = 0
    out1: int = 0
    attempts: int = 0
    sec: float = 0.0

    @property
    def solved(self) -> bool:
        return self.feasible and self.status == "verified"

    @property
    def llm(self) -> bool:
        return self.in1 > 0


def parse_pair(text: str) -> tuple[Path, Path]:
    tasks_dir, _, log = text.rpartition("=")
    if not tasks_dir:
        raise SystemExit(f"expected <tasks-dir>=<log>, got {text!r}")
    return Path(tasks_dir), Path(log)


def _read_log(log: Path) -> tuple[dict[str, dict], dict[str, list[dict]]]:
    tasks: dict[str, dict] = {}
    calls: dict[str, list[dict]] = {}
    for line in log.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec.get("kind") == "task":
            tasks[rec["task_id"]] = rec
        elif rec.get("kind") == "llm_call":
            calls.setdefault(rec["task_id"], []).append(rec)
    return tasks, calls


async def _rebuild_chars(tasks: list[Task]) -> dict[str, int]:
    prompt = Stage1Prompt(WorkspaceSnapshot(load_settings_raw()["snapshot"]))
    sem = asyncio.Semaphore(5)

    async def one(t: Task) -> tuple[str, int]:
        async with sem:
            return t.task_id, (await prompt.build(t)).prompt_chars

    return dict(await asyncio.gather(*(one(t) for t in tasks)))


def load_pair(tasks_dir: Path, log: Path) -> list[Obs]:
    labels_path = tasks_dir / "_labels.json"
    labels = json.loads(labels_path.read_text(encoding="utf-8")) if labels_path.exists() else {}
    by_id = {t.task_id: t for t in load_tasks(tasks_dir)}
    recs, calls = _read_log(log)
    need = [by_id[tid] for tid, cs in calls.items()
            if tid in by_id and "prompt_chars" not in cs[0] and "prompt_chars" not in recs.get(tid, {})]
    rebuilt = asyncio.run(_rebuild_chars(need)) if need else {}
    out = []
    for tid, rec in recs.items():
        task = by_id.get(tid)
        if task is None:
            continue
        cs = sorted(calls.get(tid, []), key=lambda c: c["stage"])
        first = cs[0] if cs else {}
        out.append(Obs(
            run=log.stem, task_id=tid, task_type=task.task_type, reward=task.reward, status=rec["status"],
            feasible=bool(labels.get(tid, {"feasible": True})["feasible"]),
            cost=sum(c["cost"] for c in cs),
            prompt_chars=int(first.get("prompt_chars") or rec.get("prompt_chars") or rebuilt.get(tid, 0)),
            in1=int(first.get("input_tokens", 0)), out1=int(first.get("output_tokens", 0)),
            attempts=len(cs), sec=float(rec.get("sec", 0.0)),
        ))
    return out


def load_pairs(pairs: list[str]) -> list[Obs]:
    obs: list[Obs] = []
    for text in pairs:
        obs += load_pair(*parse_pair(text))
    return obs
