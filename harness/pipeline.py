"""Задача по дереву решений: этап 1 → после провала ветка по DecisionPolicy (повтор этапа 1 или этап 2) → итог."""
from __future__ import annotations

from typing import Any, Callable, Protocol

from harness.decision import STOP, Decision, DecisionPolicy
from harness.outcome import Outcome
from harness.prompting import Prepared
from harness.solver import Stage1Solver
from harness.success_model import RETRY, STAGE1, STAGE2, SuccessModel
from harness.verifier import UNSOLVABLE, Verdict


class Stage2(Protocol):
    async def solve(self, prepared: Prepared, outcome: Outcome, failed: Verdict, budget: float) -> Verdict: ...


class EventLog(Protocol):
    def write(self, record: dict[str, Any]) -> None: ...


FORCED = "forced"


class TaskPipeline:
    """lam — цена токена на момент развилки (её считает Harness по ещё не начатым задачам).

    forced_problem — режим замера этапа 2 (--force-stage2): этап 1 не вызывается, этап 2 стартует с этим текстом.
    forced_branch — замер веток (--force-branch retry|stage2): после провала этапа 1 только эта ветка при λ = 0;
    после неудачного повтора — этап 2 (успех этапа 2 после retry).
    log — журнал: каждая развилка пишется событием `decision` в момент решения (его видит консоль).
    """

    def __init__(self, stage1: Stage1Solver, stage2: Stage2 | None, policy: DecisionPolicy,
                 success: SuccessModel, lam: Callable[[], float], forced_problem: str | None = None,
                 log: EventLog | None = None, forced_branch: str | None = None):
        self._s1 = stage1
        self._branch = forced_branch
        self._s2 = stage2
        self._policy = policy
        self._success = success
        self._lam = lam
        self._forced = forced_problem
        self._log = log

    async def solve(self, prepared: Prepared, outcome: Outcome) -> Outcome:
        if self._forced is not None and self._s2 is not None:
            return await self._forced_stage2(prepared, outcome)
        t = prepared.task.task_type
        verdict = await self._s1.first(prepared, outcome)
        self._apply(outcome, verdict, STAGE1)
        self._success.observe(STAGE1, t, "", verdict.ok)
        allowed = self._allowed(outcome)
        # отказ модели — конец задачи: ни повтора, ни этапа 2
        while not verdict.ok and verdict.kind != UNSOLVABLE and allowed:
            lam = 0.0 if self._branch else self._lam()
            d = self._policy.branch(prepared, verdict.kind, lam, self._forced_options(allowed))
            self._decided(prepared, outcome, d, verdict.problem)
            if d.choice == STOP:
                break
            kind = verdict.kind
            if d.choice == RETRY:
                verdict = await self._s1.retry(prepared, outcome, verdict)
                allowed = self._allowed(outcome)
            else:
                verdict = await self._s2.solve(prepared, outcome, verdict, d.budget)
                allowed = []  # после этапа 2 веток нет
            self._success.observe(d.choice, t, kind, verdict.ok)
            self._apply(outcome, verdict, d.choice)
        return outcome

    async def _forced_stage2(self, prepared: Prepared, outcome: Outcome) -> Outcome:
        outcome.messages = list(prepared.messages)
        failed = Verdict(False, "", self._forced, FORCED)
        # λ = 0: замер p₂ и цены — выгода не важна, но потолок high и резерв те же, что в обычном прогоне
        d = self._policy.branch(prepared, FORCED, 0.0, [STAGE2])
        self._decided(prepared, outcome, d)
        if d.choice == STAGE2:
            verdict = await self._s2.solve(prepared, outcome, failed, d.budget)
            self._apply(outcome, verdict, STAGE2)
        return outcome

    def _decided(self, prepared: Prepared, outcome: Outcome, d: Decision, problem: str = "") -> None:
        # начало текста провала — для разбора замеров (сам ответ модели в журнал не пишется)
        rec = {**d.as_dict(), **({"problem": problem[:300]} if problem else {})}
        outcome.decisions.append(rec)
        if self._log is not None:
            self._log.write({"kind": "decision", "task_id": prepared.task.task_id, **rec})

    def _forced_options(self, allowed: list[str]) -> list[str]:
        if self._branch is None or self._branch not in allowed:
            return allowed  # без режима замера; после повтора в режиме retry остаётся этап 2
        return [self._branch]

    def _allowed(self, outcome: Outcome) -> list[str]:
        out: list[str] = []
        if self._s1.can_retry(outcome):
            out.append(RETRY)
        if self._s2 is not None:
            out.append(STAGE2)
        return out

    @staticmethod
    def _apply(outcome: Outcome, verdict: Verdict, branch: str) -> None:
        if verdict.answer:
            outcome.answer = verdict.answer
        if verdict.kind == UNSOLVABLE:
            outcome.answer = ""  # ответ прошлой попытки не сдаём: модель сама признала, что данных нет
        outcome.verified = verdict.ok
        outcome.problem, outcome.failure = ("", "") if verdict.ok else (verdict.problem, verdict.kind)
        if verdict.ok:
            outcome.solved_by = branch

