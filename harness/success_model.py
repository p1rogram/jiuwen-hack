"""Вероятности успеха веток дерева решений и выполнимости задачи с soft-сигналом — бета-априор + факт прогона."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

STAGE1, RETRY, STAGE2 = "stage1", "retry", "stage2"
DEFAULT = "default"


class SuccessModel:
    """Параметры — config/success_model.json (пишет tools/calibrate_success.py).

    Ключ ветки ищется от частного к общему: «тип/вид провала», «вид провала», «тип», default.
    Факт прогона копится по точному ключу (ветка, тип, вид) и прибавляется к найденному априору.
    """

    def __init__(self, cfg: dict[str, Any]):
        self._branches: dict[str, dict[str, tuple[float, float]]] = {
            b: {k: (float(v["alpha"]), float(v["beta"])) for k, v in keys.items()}
            for b, keys in cfg["branches"].items()}
        self._feasible = {k: (float(v["alpha"]), float(v["beta"])) for k, v in cfg["p_feasible"].items()}
        self._obs: dict[tuple[str, str, str], list[int]] = defaultdict(lambda: [0, 0])

    def _prior(self, branch: str, task_type: str, kind: str) -> tuple[float, float]:
        keys = self._branches.get(branch) or self._branches[DEFAULT]
        for key in (f"{task_type}/{kind}", kind, task_type, DEFAULT):
            if key and key in keys:
                return keys[key]
        return self._branches[DEFAULT][DEFAULT]

    def p(self, branch: str, task_type: str, kind: str = "") -> float:
        a, b = self._prior(branch, task_type, kind)
        ok, n = self._obs[(branch, task_type, kind)]
        return (a + ok) / (a + b + n)

    def observe(self, branch: str, task_type: str, kind: str, success: bool) -> None:
        cell = self._obs[(branch, task_type, kind)]
        cell[0] += int(success)
        cell[1] += 1

    def p_feasible(self, soft_codes: Iterable[str]) -> float:
        """Самый осторожный из сигналов: задача выполнима, только если ни один из них не оказался правдой."""
        ps = [a / (a + b) for a, b in (self._feasible.get(c) or self._feasible[DEFAULT] for c in soft_codes)]
        return min(ps) if ps else 1.0
