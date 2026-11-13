"""Детектор невыполнимых задач на этапе 0: общая проверка файлов + проверка по типу задачи. Без LLM."""
from __future__ import annotations

from typing import Any, Iterable

from harness.feasibility.coding import CodingCheck
from harness.feasibility.data import DataCheck
from harness.feasibility.files import ReferencedFilesCheck
from harness.feasibility.math_check import MathCheck
from harness.feasibility.model import INFO, Signal
from harness.feasibility.search import SearchCheck
from harness.tasks import Task


class FeasibilityDetector:
    def __init__(self, cfg: dict[str, Any], search_evidence: bool = False):
        self._search_evidence = search_evidence
        self._common = ReferencedFilesCheck(cfg["files"])
        self._by_type = {
            "coding": CodingCheck(),
            "data_analysis": DataCheck(cfg["data"]),
            "math": MathCheck(cfg["math"]),
            "search": SearchCheck(cfg["search"]),
        }

    def check(self, task: Task) -> list[Signal]:
        if self._search_evidence and task.task_type == 'search':
            return [s for s in self._common.check(task) if s.severity == 'hard']
        signals: list[Signal] = []
        for checker in (self._common, self._by_type.get(task.task_type)):
            if checker is None:
                continue
            try:
                signals += checker.check(task)
            except Exception as exc:  # сбой разбора не повод выбрасывать задачу
                signals.append(Signal("detector_error", INFO, f"{type(checker).__name__}: {type(exc).__name__}: {exc}"[:300]))
        return signals

    def missing_column(self, task: Task, error: str) -> str | None:
        """Объявленный условием столбец из KeyError скрипта, которого нет в CSV (для вида провала в Verifier)."""
        return self._by_type["data_analysis"].missing_declared(task, error)


def blocking(signals: Iterable[Signal], skip_severities: Iterable[str]) -> list[Signal]:
    skip = set(skip_severities)
    return [s for s in signals if s.severity in skip]
