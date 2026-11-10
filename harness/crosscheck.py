"""Перекрёстная проверка ответа math/data/search: второе независимое решение, при расхождении — третье, большинство.

Второе мнение — тот же этап 1 (скрипт по тем же сообщениям) другой моделью: у data весь CSV в промпт не входит,
поэтому ответ «без скрипта» здесь не годится. Ответ этапа 1 меняется, только если третье решение совпало со вторым.
"""
from __future__ import annotations

from typing import Any, Protocol

from harness.budget import BudgetLedger
from harness.model_call import BudgetExhausted
from harness.outcome import Outcome
from harness.prompting import Prepared
from harness.verifier import Verdict


class Solver(Protocol):
    async def first(self, prepared: Prepared, outcome: Outcome) -> Verdict: ...


class EventLog(Protocol):
    def write(self, record: dict[str, Any]) -> None: ...


def normalize(answer: str) -> str:
    text = answer.strip().lower()
    try:
        num = float(text.replace(",", ""))
    except ValueError:
        return text
    return str(int(num)) if num.is_integer() else str(num)


class CrossCheck:
    def __init__(self, second: Solver, arbiter: Solver, cfg: dict[str, Any], ledger: BudgetLedger, log: EventLog):
        self._second = second
        self._arbiter = arbiter
        self._types = set(cfg["types"])
        # перекрёстная проверка — роскошь: при малом свободном остатке токены нужнее нерешённым задачам
        self._min_available = float(cfg["min_available_tokens"])
        self._ledger = ledger
        self._log = log

    def applies(self, prepared: Prepared, outcome: Outcome) -> bool:
        return (outcome.verified and bool(outcome.answer) and prepared.task.task_type in self._types
                and self._ledger.available >= self._min_available)

    async def review(self, prepared: Prepared, outcome: Outcome) -> None:
        """Меняет outcome.answer только по большинству; сбой или исчерпанный бюджет — ответ этапа 1 остаётся."""
        if not self.applies(prepared, outcome):
            return
        first = outcome.answer
        rec: dict[str, Any] = {"kind": "crosscheck", "task_id": prepared.task.task_id}
        try:
            second = await self._opinion(self._second, prepared, outcome)
            rec["second"] = second
            if second is None or normalize(second) == normalize(first):
                rec["result"] = "agree" if second is not None else "second_failed"
                return
            third = await self._opinion(self._arbiter, prepared, outcome)
            rec["third"] = third
            if third is not None and normalize(third) == normalize(second) != normalize(first):
                outcome.answer = second
                rec["result"] = "replaced"
            else:
                rec["result"] = "kept"
        except BudgetExhausted:
            rec["result"] = "budget_exhausted"
        except Exception as exc:  # проверка не должна отнимать уже найденный ответ
            rec["result"] = "error"
            rec["error"] = f"{type(exc).__name__}: {exc}"[:300]
        finally:
            self._log.write({**rec, "first": first, "answer": outcome.answer})

    @staticmethod
    async def _opinion(solver: Solver, prepared: Prepared, outcome: Outcome) -> str | None:
        scratch = Outcome(outcome.task_id)
        try:
            verdict = await solver.first(prepared, scratch)
        finally:
            outcome.cost += scratch.cost  # оплаченные вызовы — в итог задачи и при сбое
            outcome.calls += scratch.calls
        return verdict.answer if verdict.ok and verdict.answer else None
