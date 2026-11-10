"""math без LLM: склад и цепочка операций, каждое предложение до вопроса — по шаблону целиком."""
from __future__ import annotations

import re
from typing import Any

from harness.rules.common import split_question
from harness.tasks import Task


def apply(op: str, stock: int, a: int, b: int) -> int | None:
    if op == "sub_mul":
        return stock - a * b
    if op == "add_mul":
        return stock + a * b
    if op == "sub":
        return stock - a
    if op == "floordiv":
        return stock // a if a > 0 else None
    if op == "double":
        return stock * 2
    return None


class MathRules:
    def __init__(self, cfg: dict[str, Any], tail: list[re.Pattern]):
        self._tail = tail
        self._start = re.compile(cfg["start"])
        self._ops = cfg["ops"]
        self._question = cfg["question"]

    def solve(self, task: Task) -> str | None:
        parts = split_question(task.instruction, self._tail)
        if parts is None:
            return None
        before, question = parts
        if not before:
            return None
        m = self._start.match(before[0])
        if not m:
            return None
        stock, unit = int(m.group("n")), re.escape(m.group("unit"))
        if not re.match(self._question.replace("{unit}", unit), question):
            return None
        ops = [(re.compile(o["re"].replace("{unit}", unit)), o["op"]) for o in self._ops]
        for sentence in before[1:]:
            stock = self._step(ops, sentence, stock)
            if stock is None or stock < 0:
                return None  # отрицательный остаток — условие не такое, как кажется
        return str(stock)

    @staticmethod
    def _step(ops: list[tuple[re.Pattern, str]], sentence: str, stock: int) -> int | None:
        for rx, op in ops:
            m = rx.match(sentence)
            if m:
                g = m.groupdict()
                return apply(op, stock, int(g.get("a") or 0), int(g.get("b") or 0))
        return None
