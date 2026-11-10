"""data_analysis без LLM: сумма по CSV, когда условие, шапка и число строк совпадают с шаблоном."""
from __future__ import annotations

import csv
import difflib
import re
from typing import Any

from harness.rules.common import split_question
from harness.tasks import Task


class DataRules:
    def __init__(self, cfg: dict[str, Any]):
        self._intro = re.compile(cfg["intro"])
        self._questions = [(re.compile(q["re"]), q["sum"]) for q in cfg["questions"]]
        self._month = cfg["month_column"]
        self._filters = cfg["filter_columns"]
        self._alias_cutoff = cfg["alias_cutoff"]

    def solve(self, task: Task) -> str | None:
        parts = split_question(task.instruction)
        if parts is None or len(parts[0]) != 1:
            return None
        intro = self._intro.match(parts[0][0])
        if not intro:
            return None
        found = next(((m, cols) for rx, cols in self._questions if (m := rx.match(parts[1]))), None)
        if found is None:
            return None
        q, sum_cols = found
        path = task.workspace / intro.group("file")
        if not path.is_file():
            return None
        declared = intro.group("cols").split(",")
        with path.open(encoding="utf-8", newline="") as fh:
            rows = list(csv.reader(fh))
        # шапка как в условии и столько строк, сколько обещано, — иначе файл не тот, что описан
        if not rows or rows[0] != declared or len(rows) - 1 != int(intro.group("rows")):
            return None
        idx = {c: i for i, c in enumerate(declared)}
        want = {c: v for c, v in q.groupdict().items() if c in self._filters and v is not None}
        if any(c not in idx for c in [*want, *sum_cols, self._month]):
            return None
        m1, m2 = int(q.group("m1")), int(q.group("m2"))
        # месяцы за 12 — сумма по пустому диапазону (так отвечает и LLM: «0»); обратный порядок спорен
        if not 1 <= m1 <= m2:
            return None
        if any(self._alias(rows[1:], idx[c], v) for c, v in want.items()):
            return None
        return self._total(rows[1:], idx, want, sum_cols, m1, m2)

    def _alias(self, rows: list[list[str]], col: int, value: str) -> bool:
        """Значения фильтра нет в столбце, но есть похожее (регистр, опечатка, множественное число) — это может быть
        то же самое под другим именем; такую задачу решает LLM, а не ответ «0»."""
        values = {row[col] for row in rows if len(row) > col}
        if value in values:
            return False
        lowered = [v.lower() for v in values]
        return value.lower() in lowered or bool(difflib.get_close_matches(value.lower(), lowered, 1, self._alias_cutoff))

    def _total(self, rows: list[list[str]], idx: dict[str, int], want: dict[str, str], sum_cols: list[str],
               m1: int, m2: int) -> str | None:
        total = 0
        for row in rows:
            if len(row) != len(idx):
                return None
            try:
                month = int(row[idx[self._month]])
                values = [int(row[idx[c]]) for c in sum_cols]
            except ValueError:
                return None  # нецелое или пустое значение — формат ответа не очевиден
            if any(row[idx[c]] != v for c, v in want.items()):
                continue
            if m1 <= month <= m2:
                prod = 1
                for v in values:
                    prod *= v
                total += prod
        # ни одной подходящей строки — «0»: значение фильтра проверено на похожие в _alias
        return str(total)
