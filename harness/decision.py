"""Развилки дерева решений по EMV = p·reward − λ·цена, где λ — цена токена в баллах при текущем остатке бюджета.

Без LLM и ввода-вывода: вероятности — SuccessModel, цены — CostModel, допуск ветки — резерв в BudgetLedger.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from harness.budget import BudgetLedger
from harness.cost_model import CostModel, Estimate
from harness.feasibility import SOFT
from harness.prompting import Prepared
from harness.success_model import RETRY, STAGE1, STAGE2, SuccessModel

STOP = "stop"


@dataclass(frozen=True)
class Branch:
    name: str
    p: float
    cost: float
    high: float
    emv: float
    fits: bool | None = None  # None — до проверки резерва не дошло

    def as_dict(self) -> dict:
        return {"name": self.name, "p": round(self.p, 3), "cost": round(self.cost, 1), "high": round(self.high, 1),
                "emv": round(self.emv, 3), "fits": self.fits}


@dataclass
class Decision:
    failure: str
    lam: float
    choice: str = STOP
    budget: float = 0.0  # high выбранной ветки — потолок её расхода
    branches: list[Branch] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"failure": self.failure, "lambda": round(self.lam, 5), "choice": self.choice,
                "branches": [b.as_dict() for b in self.branches]}


def soft_codes(p: Prepared) -> list[str]:
    return [s.code for s in p.signals if getattr(s, "severity", "") == SOFT]


class DecisionPolicy:
    def __init__(self, success: SuccessModel, cost: CostModel, ledger: BudgetLedger):
        self._p = success
        self._cost = cost
        self._ledger = ledger

    def _branch_cost(self, name: str, p: Prepared) -> Estimate:
        t, chars = p.task.task_type, p.prompt_chars
        return self._cost.retry_cost(t, chars) if name == RETRY else self._cost.stage2_cost(t, chars)

    def p_feasible(self, p: Prepared) -> float:
        return self._p.p_feasible(soft_codes(p))

    def p_success(self, p: Prepared) -> float:
        """Этап 1 или (после провала) этап 2 — ветка эскалации по умолчанию."""
        t = p.task.task_type
        p1 = self._p.p(STAGE1, t)
        return self.p_feasible(p) * (p1 + (1 - p1) * self._p.p(STAGE2, t))

    def expected_cost(self, p: Prepared) -> float:
        t = p.task.task_type
        c2 = self._cost.stage2_cost(t, p.prompt_chars).mean
        tail = (1 - self._p.p(STAGE1, t)) * c2 if c2 != float("inf") else 0.0
        return self._cost.estimate(t, p.prompt_chars).mean + tail

    def value(self, p: Prepared) -> float:
        return p.task.reward * self.p_success(p)

    def priority(self, p: Prepared) -> float:
        return self.value(p) / max(self.expected_cost(p), 1.0)

    def journal(self, p: Prepared) -> dict:
        return {"p_success": round(self.p_success(p), 3), "p_feasible": round(self.p_feasible(p), 3)}

    def lambda_(self, remaining: Iterable[Prepared], available: float) -> float:
        """0 — бюджета хватает на все оставшиеся; иначе выгода на токен первой задачи вне жадного рюкзака."""
        items = [(self.value(p), self.expected_cost(p)) for p in remaining]
        if sum(e for _, e in items) <= available:
            return 0.0
        used = 0.0
        for v, e in sorted(items, key=lambda x: -x[0] / max(x[1], 1.0)):
            if used + e > available:
                return v / max(e, 1.0)
            used += e
        return 0.0

    def skip_soft(self, p: Prepared, lam: float) -> bool:
        """Задачу с soft-сигналом пропускаем, если ожидаемая выгода меньше цены её токенов."""
        if not soft_codes(p):
            return False
        return self.value(p) < lam * self.expected_cost(p)

    def branch(self, p: Prepared, failure: str, lam: float, allowed: Iterable[str]) -> Decision:
        """После провала: лучшая по EMV ветка из allowed, чей high помещается в бюджет (резерв ставится), иначе stop."""
        t, reward, pf = p.task.task_type, p.task.reward, self.p_feasible(p)
        d = Decision(failure, lam)
        options = []
        for name in allowed:
            est = self._branch_cost(name, p)
            prob = self._p.p(name, t, failure)
            options.append(Branch(name, prob, est.mean, est.high, pf * prob * reward - lam * est.mean))
        options.sort(key=lambda b: -b.emv)
        for b in options:
            if d.choice == STOP and b.emv > 0 and b.high != float("inf"):
                fits = self._ledger.reserve(p.task.task_id, b.high)
                if fits:
                    d.choice, d.budget = b.name, b.high
                b = Branch(b.name, b.p, b.cost, b.high, b.emv, fits)
            d.branches.append(b)
        return d
