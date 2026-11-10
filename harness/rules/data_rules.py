"""data_analysis без LLM: сумма по CSV, когда условие, шапка и число строк совпадают с шаблоном."""
from __future__ import annotations

import csv
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
        if not (1 <= m1 <= m2 <= 12):
            return None
        return self._total(rows[1:], idx, want, sum_cols, m1, m2)

    def _total(self, rows: list[list[str]], idx: dict[str, int], want: dict[str, str], sum_cols: list[str],
               m1: int, m2: int) -> str | None:
        total, matched = 0, False
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
            matched = True
            if m1 <= month <= m2:
                prod = 1
                for v in values:
                    prod *= v
                total += prod
        # фильтра нет ни в одной строке — спорная задача (ответ «0»?), её решает обычный путь
        return str(total) if matched else None
