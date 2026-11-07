"""math: в условии есть все числа, нет деления на 0, спрашивают о величине из условия."""
from __future__ import annotations

import re
from typing import Any

from harness.feasibility.model import HARD, SOFT, Signal
from harness.tasks import Task


class MathCheck:
    def __init__(self, cfg: dict[str, Any]):
        self._vague = re.compile(cfg["vague_re"])
        self._zero = [re.compile(rx) for rx in cfg["zero_res"]]
        self._unit = re.compile(cfg["unit_re"])

    def check(self, task: Task) -> list[Signal]:
        text = task.instruction.lower()
        cut = text.find("?")
        problem = text[: cut + 1] if cut >= 0 else text
        # «how many» — вопрос, а не неопределённое количество
        scan = problem.replace("how many", "")
        signals = []
        m = self._vague.search(scan)
        if m:
            signals.append(Signal("vague_quantity", HARD, m.group(0)))
        for rx in self._zero:
            z = rx.search(problem)
            if z:
                signals.append(Signal("div_zero", HARD, z.group(0)))
                break
        u = self._unit.search(problem)
        if u:
            unit = u.group(1)
            before = problem[: u.start()]
            stem = unit[:-1] if unit.endswith("s") else unit
            if stem not in before:
                signals.append(Signal("unknown_unit", SOFT, f"asks about {unit!r} not mentioned before"))
        return signals
