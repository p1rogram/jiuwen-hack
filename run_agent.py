"""Харнесс openJiuwen Challenge: решает задачи из --tasks-dir в рамках общего бюджета токенов.

Запуск: python run_agent.py --tasks-dir /data/tasks [--budget N] [--out FILE]
"""
from __future__ import annotations

import argparse
import asyncio
import time
from pathlib import Path

import harness.jiuwen_logging  # noqa: F401  — первым: openjiuwen не печатает в консоль
from harness.budget import BudgetLedger, TokenPricing
from harness.calllog import JsonlLog
from harness.console import ConsoleView
from harness.cost_model import CostModel
from harness.decision import DecisionPolicy
from harness.feasibility import FeasibilityDetector, Signal, blocking
from harness.llm import BudgetExhausted, LlmGateway
from harness.outcome import Outcome
from harness.pipeline import TaskPipeline
from harness.planner import TaskPlanner
from harness.prompting import Prepared, Stage1Prompt
from harness.results import ResultsWriter
from harness.settings import Settings, load_config, load_prompt, load_settings
from harness.snapshot import WorkspaceSnapshot
from harness.solver import Stage1Solver
from harness.stage2 import Stage2Solver
from harness.success_model import SuccessModel
from harness.tasks import Task, load_tasks
from harness.verifier import UNSOLVABLE, Verifier


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--tasks-dir", required=True, type=Path)
    p.add_argument("--budget", type=float, default=None, help="бюджет в выходных токенах")
    p.add_argument("--out", default=None, help="файл решений (по умолчанию env OUT)")
    p.add_argument("--model", default=None, help="модель этапа 1 вместо config")
    p.add_argument("--profile", default=None,
                   help="профиль стенда из config/profiles (по умолчанию env HARNESS_PROFILE), например spark")
    p.add_argument("--only", nargs="*", default=None, help="решать только эти task_id")
    p.add_argument("--force-stage2", action="store_true",
                   help="замер этапа 2: этап 1 не вызывается, каждая задача сразу идёт в этап 2")
    return p.parse_args()


class Harness:
    profile: str | None = None  # профиль стенда для run_start; тесты собирают Harness без __init__

    def __init__(self, settings: Settings, model: str | None, force_stage2: bool = False):
        acc = settings.section("accounting")
        self.ledger = BudgetLedger(settings.budget)
        run_id = time.strftime("%Y%m%d-%H%M%S")
        self.log = JsonlLog(settings.log_dir / f"run-{run_id}.jsonl")
        gateway = LlmGateway(settings.base_url, settings.api_key, settings.section("llm"),
                             TokenPricing(acc["input_weight"], acc["reasoning_in_completion"]),
                             self.ledger, self.log, settings.raw["model_map"],
                             acc["missing_usage_chars_per_token"])
        stage1_cfg = settings.section("stage1")
        marker = stage1_cfg["unsolvable_marker"] if stage1_cfg["unsolvable_enabled"] else None
        self.prompt = Stage1Prompt(WorkspaceSnapshot(settings.section("snapshot")), marker, stage1_cfg["tail_prompts"])
        verifier = Verifier(stage1_cfg["error_tail_chars"], marker)
        models = settings.section("models")
        stage1 = Stage1Solver(gateway, self.prompt, verifier, model or models["stage1"], stage1_cfg)
        cost_model = CostModel(load_config(settings.raw["cost_model_file"]), acc["input_weight"])
        stage2 = Stage2Solver(gateway, verifier, cost_model, model or models["stage2"], settings.section("stage2"))
        success = SuccessModel(load_config(settings.raw["success_model_file"]))
        self.policy = DecisionPolicy(success, cost_model, self.ledger)
        self.solver = TaskPipeline(stage1, stage2, self.policy, success, self.lam,
                                   load_prompt("stage2_forced.txt") if force_stage2 else None, self.log)
        self.planner = TaskPlanner(cost_model, self.ledger, self.policy.priority)
        self.pending: dict[str, Prepared] = {}  # ещё не начатые задачи — по ним считается λ
        self.writer = ResultsWriter(settings.out_path)
        self.parallel = int(settings.raw["parallel_sessions"])
        self.profile = settings.profile
        feas = settings.section("feasibility")
        self.detector = FeasibilityDetector(load_config(feas["config_file"])) if feas["enabled"] else None
        self.skip_severities = feas["skip_severities"]

    async def run(self, tasks: list[Task]) -> list[dict]:
        self.log.write({"kind": "run_start", "tasks": len(tasks), "budget": self.ledger.total,
                        **({"profile": self.profile} if self.profile else {})})
        sem = asyncio.Semaphore(self.parallel)

        async def limited(coro):
            async with sem:
                return await coro

        # фаза A без LLM: отсев невыполнимых и сборка промптов — нужна до выбора, цена входа зависит от промпта
        staged = await asyncio.gather(*(limited(self._prepare(t)) for t in tasks))
        skipped = [s for s in staged if isinstance(s, dict)]
        prepared = self.planner.order([s for s in staged if isinstance(s, Prepared)])
        self.pending = {p.task.task_id: p for p in prepared}
        # soft-задача, чья ожидаемая выгода меньше цены её токенов при текущем λ, — не начинаем вовсе
        lam = self.lam()
        low = [p for p in prepared if self.policy.skip_soft(p, lam)]
        for p in low:
            self.pending.pop(p.task.task_id)
            skipped.append(self._record({"task_id": p.task.task_id, "type": p.task.task_type, "reward": p.task.reward,
                                         "status": "skipped_low_value", "lambda": round(lam, 5),
                                         **self.policy.journal(p), "signals": [s.as_dict() for s in p.signals]}))
        prepared = [p for p in prepared if p.task.task_id in self.pending]
        # фаза B: старт в порядке выгоды, допуск каждой задачи — в момент старта, по оценке с поправкой
        first = await asyncio.gather(*(limited(self._solve_one(p, final=False)) for p in prepared))
        # не поместившиеся — ещё раз, когда резервы задач в работе сменились фактической ценой
        deferred = [p for p, r in zip(prepared, first) if r is None]
        second = await asyncio.gather(*(limited(self._solve_one(p, final=True)) for p in deferred))
        return skipped + [r for r in first if r is not None] + list(second)

    def lam(self) -> float:
        """Цена токена в баллах сейчас: по ещё не начатым задачам и свободному остатку бюджета."""
        return self.policy.lambda_(list(self.pending.values()), self.ledger.available)

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
            self.pending.pop(task.task_id, None)
            e = self.planner.estimate(prepared)
            return self._record({**head, "status": "skipped_budget", "estimate_mean": round(e.mean, 1),
                                 "estimate_high": round(e.high, 1), "available": round(self.ledger.available, 1)})
        self.pending.pop(task.task_id, None)
        head |= {"estimate_mean": round(est.mean, 1), "estimate_high": round(est.high, 1),
                 "lambda": round(self.lam(), 5), **self.policy.journal(prepared)}
        self.log.write({"kind": "task_start", "task_id": task.task_id, "estimate_high": head["estimate_high"],
                        "lambda": head["lambda"]})
        started = time.monotonic()
        # Outcome создаётся здесь: при исключении в нём остаются уже оплаченные вызовы — для журнала и поправки
        outcome = Outcome(task.task_id)
        try:
            await self.solver.solve(prepared, outcome)
        except BudgetExhausted:
            self.writer.discard(task)
            return self._record({**head, "status": "budget_exhausted", "cost": round(outcome.cost, 1),
                                 "calls": outcome.calls})
        except Exception as exc:  # одна упавшая задача не должна валить прогон
            self.writer.discard(task)
            return self._record({**head, "status": "error", "error": f"{type(exc).__name__}: {exc}"[:500],
                                 "cost": round(outcome.cost, 1), "calls": outcome.calls})
        finally:
            # поправка модели расхода этапа 1 — только по его первому вызову (в --force-stage2 его нет)
            self.planner.finish(prepared, outcome.first_stage1_call())
        if outcome.answer:
            self.writer.record(task, outcome.answer)
        else:
            self.writer.discard(task)
        status = "verified" if outcome.verified else "unsolvable" if outcome.failure == UNSOLVABLE else "unverified"
        return self._record({**head, "status": status,
                             "answer": outcome.answer, "solved_by": outcome.solved_by, "attempts": outcome.attempts,
                             "cost": round(outcome.cost, 1),
                             "problem": outcome.problem[:300], "failure": outcome.failure,
                             "decisions": outcome.decisions, "stage2_steps": outcome.stage2_steps,
                             "stage2_stop": outcome.stage2_stop, "calls": outcome.calls,
                             "signals": [s.as_dict() for s in prepared.signals],
                             "sec": round(time.monotonic() - started, 1)})


def main() -> None:
    args = parse_args()
    settings = load_settings(budget=args.budget, out=args.out, profile=args.profile)
    tasks = load_tasks(args.tasks_dir)
    if args.only:
        tasks = [t for t in tasks if t.task_id in set(args.only)]
    harness = Harness(settings, args.model, args.force_stage2)
    # консоль только смотрит журнал: строки задач и развилок по ходу, прогресс в stderr, сводка в конце
    console = ConsoleView(load_config("console.json"), harness.ledger)
    harness.log.subscribe(console)
    asyncio.run(run_with_console(harness, tasks, console))
    console.summary()


async def run_with_console(harness: Harness, tasks: list[Task], console: ConsoleView) -> list[dict]:
    async with console.live():
        return await harness.run(tasks)


if __name__ == "__main__":
    main()
