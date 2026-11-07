"""Харнесс openJiuwen Challenge: решает задачи из --tasks-dir в рамках общего бюджета токенов.

Запуск: python run_agent.py --tasks-dir /data/tasks [--budget N] [--out FILE]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

from harness.budget import BudgetLedger, TokenPricing
from harness.calllog import JsonlLog
from harness.llm import BudgetExhausted, LlmGateway
from harness.planner import TaskPlanner
from harness.results import ResultsWriter
from harness.settings import Settings, load_settings
from harness.snapshot import WorkspaceSnapshot
from harness.solver import Stage1Solver
from harness.tasks import Task, load_tasks
from harness.verifier import Verifier


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--tasks-dir", required=True, type=Path)
    p.add_argument("--budget", type=float, default=None, help="бюджет в выходных токенах")
    p.add_argument("--out", default=None, help="файл решений (по умолчанию env OUT)")
    p.add_argument("--model", default=None, help="модель этапа 1 вместо config")
    p.add_argument("--only", nargs="*", default=None, help="решать только эти task_id")
    return p.parse_args()


class Harness:
    def __init__(self, settings: Settings, model: str | None):
        acc = settings.section("accounting")
        self.ledger = BudgetLedger(settings.budget)
        run_id = time.strftime("%Y%m%d-%H%M%S")
        self.log = JsonlLog(settings.log_dir / f"run-{run_id}.jsonl")
        gateway = LlmGateway(settings.base_url, settings.api_key, settings.section("llm"),
                             TokenPricing(acc["input_weight"], acc["reasoning_in_completion"]),
                             self.ledger, self.log)
        stage1_cfg = settings.section("stage1")
        self.solver = Stage1Solver(gateway, WorkspaceSnapshot(settings.section("snapshot")),
                                   Verifier(stage1_cfg["error_tail_chars"]),
                                   model or settings.section("models")["stage1"], stage1_cfg)
        self.planner = TaskPlanner(settings.section("cost_estimate_by_type"))
        self.writer = ResultsWriter(settings.out_path)
        self.parallel = int(settings.raw["parallel_sessions"])

    async def run(self, tasks: list[Task]) -> list[dict]:
        sem = asyncio.Semaphore(self.parallel)
        ordered = self.planner.order(tasks)

        async def one(task: Task) -> dict:
            async with sem:
                return await self._solve_one(task)

        return await asyncio.gather(*(one(t) for t in ordered))

    async def _solve_one(self, task: Task) -> dict:
        estimate = self.planner.estimate(task)
        if not self.ledger.can_afford(estimate):
            rec = {"task_id": task.task_id, "status": "skipped_budget", "estimate": estimate}
            self.log.write({"kind": "task", **rec})
            return rec
        started = time.monotonic()
        try:
            outcome = await self.solver.solve(task)
        except BudgetExhausted:
            rec = {"task_id": task.task_id, "status": "budget_exhausted"}
            self.log.write({"kind": "task", **rec})
            return rec
        except Exception as exc:  # одна упавшая задача не должна валить прогон
            rec = {"task_id": task.task_id, "status": "error", "error": f"{type(exc).__name__}: {exc}"[:500]}
            self.log.write({"kind": "task", **rec})
            return rec
        if outcome.answer:
            self.writer.record(task, outcome.answer)
        rec = {"task_id": task.task_id, "type": task.task_type, "reward": task.reward,
               "status": "verified" if outcome.verified else "unverified", "answer": outcome.answer,
               "attempts": outcome.attempts, "cost": round(outcome.cost, 1), "estimate": estimate,
               "problem": outcome.problem[:300], "calls": outcome.calls,
               "sec": round(time.monotonic() - started, 1)}
        self.log.write({"kind": "task", **rec})
        return rec


def main() -> None:
    args = parse_args()
    settings = load_settings(budget=args.budget, out=args.out)
    tasks = load_tasks(args.tasks_dir)
    if args.only:
        tasks = [t for t in tasks if t.task_id in set(args.only)]
    harness = Harness(settings, args.model)
    results = asyncio.run(harness.run(tasks))
    for r in results:
        print(f"{r['task_id']:<22} {r['status']:<16} cost={r.get('cost', 0):>7} answer={r.get('answer', '')!r}",
              file=sys.stderr)
    print(f"spent {harness.ledger.spent:.0f} of {harness.ledger.total:.0f}", file=sys.stderr)


if __name__ == "__main__":
    main()
