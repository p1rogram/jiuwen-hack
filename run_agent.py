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
from harness.cost_model import CostModel
from harness.feasibility import FeasibilityDetector, Signal, blocking
from harness.llm import BudgetExhausted, LlmGateway
from harness.planner import TaskPlanner
from harness.prompting import Prepared, Stage1Prompt
from harness.results import ResultsWriter
from harness.settings import Settings, load_config, load_settings
from harness.snapshot import WorkspaceSnapshot
from harness.solver import Outcome, Stage1Solver
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
        self.prompt = Stage1Prompt(WorkspaceSnapshot(settings.section("snapshot")))
        self.solver = Stage1Solver(gateway, self.prompt, Verifier(stage1_cfg["error_tail_chars"]),
                                   model or settings.section("models")["stage1"], stage1_cfg)
        cost_model = CostModel(load_config(settings.raw["cost_model_file"]), acc["input_weight"])
        self.planner = TaskPlanner(cost_model, self.ledger)
        self.writer = ResultsWriter(settings.out_path)
        self.parallel = int(settings.raw["parallel_sessions"])
        feas = settings.section("feasibility")
        self.detector = FeasibilityDetector(load_config(feas["config_file"])) if feas["enabled"] else None
        self.skip_severities = feas["skip_severities"]

    async def run(self, tasks: list[Task]) -> list[dict]:
        sem = asyncio.Semaphore(self.parallel)

        async def limited(coro):
            async with sem:
                return await coro

        # фаза A без LLM: отсев невыполнимых и сборка промптов — нужна до выбора, цена входа зависит от промпта
        staged = await asyncio.gather(*(limited(self._prepare(t)) for t in tasks))
        skipped = [s for s in staged if isinstance(s, dict)]
        prepared = self.planner.order([s for s in staged if isinstance(s, Prepared)])
        # фаза B: старт в порядке выгоды, допуск каждой задачи — в момент старта, по оценке с поправкой
        first = await asyncio.gather(*(limited(self._solve_one(p, final=False)) for p in prepared))
        # не поместившиеся — ещё раз, когда резервы задач в работе сменились фактической ценой
        deferred = [p for p, r in zip(prepared, first) if r is None]
        second = await asyncio.gather(*(limited(self._solve_one(p, final=True)) for p in deferred))
        return skipped + [r for r in first if r is not None] + list(second)

    def _record(self, rec: dict) -> dict:
        self.log.write({"kind": "task", **rec})
        return rec

    async def _prepare(self, task: Task) -> Prepared | dict:
        try:
            signals: list[Signal] = await asyncio.to_thread(self.detector.check, task) if self.detector else []
            if blocking(signals, self.skip_severities):
                return self._record({"task_id": task.task_id, "type": task.task_type, "reward": task.reward,
                                     "status": "skipped_infeasible", "signals": [s.as_dict() for s in signals]})
            prepared = await self.prompt.build(task)
        except Exception as exc:  # одна упавшая задача не должна валить прогон: фаза A общая для всех
            return self._record({"task_id": task.task_id, "status": "error",
                                 "error": f"prepare {type(exc).__name__}: {exc}"[:500]})
        prepared.signals = signals
        return prepared

    async def _solve_one(self, prepared: Prepared, final: bool) -> dict | None:
        """None — не поместилась и final=False: задача отложена до второго прохода."""
        task = prepared.task
        head = {"task_id": task.task_id, "type": task.task_type, "reward": task.reward,
                "prompt_chars": prepared.prompt_chars}
        est = self.planner.admit(prepared)
        if est is None:
            if not final:
                return None
            e = self.planner.estimate(prepared)
            return self._record({**head, "status": "skipped_budget", "estimate_mean": round(e.mean, 1),
                                 "estimate_high": round(e.high, 1), "available": round(self.ledger.available, 1)})
        head |= {"estimate_mean": round(est.mean, 1), "estimate_high": round(est.high, 1)}
        started = time.monotonic()
        # Outcome создаётся здесь: при исключении в нём остаются уже оплаченные вызовы — для журнала и поправки
        outcome = Outcome(task.task_id)
        try:
            await self.solver.solve(prepared, outcome)
        except BudgetExhausted:
            return self._record({**head, "status": "budget_exhausted", "cost": round(outcome.cost, 1),
                                 "calls": outcome.calls})
        except Exception as exc:  # одна упавшая задача не должна валить прогон
            return self._record({**head, "status": "error", "error": f"{type(exc).__name__}: {exc}"[:500],
                                 "cost": round(outcome.cost, 1), "calls": outcome.calls})
        finally:
            self.planner.finish(prepared, outcome.calls[0] if outcome.calls else None)
        if outcome.answer:
            self.writer.record(task, outcome.answer)
        return self._record({**head, "status": "verified" if outcome.verified else "unverified",
                             "answer": outcome.answer, "attempts": outcome.attempts, "cost": round(outcome.cost, 1),
                             "problem": outcome.problem[:300], "calls": outcome.calls,
                             "signals": [s.as_dict() for s in prepared.signals],
                             "sec": round(time.monotonic() - started, 1)})


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
