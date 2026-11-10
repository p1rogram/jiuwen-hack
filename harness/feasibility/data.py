"""data_analysis: в CSV есть столбцы и значения, о которых спрашивают."""
from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any

from harness.feasibility.model import HARD, SOFT, Signal, find_file, questions
from harness.tasks import Task

CSV_NAME_RE = re.compile(r"(?<![\w/.-])((?:[\w-]+/)*[\w-]+\.(?:csv|tsv))\b")


def mentions(text: str, column: str) -> bool:
    """Имя столбца словом в тексте: `unit_cost` — и как «unit cost»."""
    return any(re.search(rf"\b{re.escape(name)}\b", text) for name in {column, column.replace("_", " ")})


class DataCheck:
    def __init__(self, cfg: dict[str, Any]):
        self._declared_re = re.compile(cfg["columns_declared_re"])
        self._triggers = cfg["column_triggers"]
        self._filters = {col: re.compile(rx) for col, rx in cfg["value_filters"].items()}
        self._months_re = re.compile(cfg["months_re"])
        self._max_bytes = int(cfg["max_bytes"])

    def check(self, task: Task) -> list[Signal]:
        files = [p for p in (find_file(task, n) for n in CSV_NAME_RE.findall(task.instruction)) if p and p.is_file()]
        question = " ".join(questions(task.instruction)) or task.instruction
        signals = self._months(question)
        if len(files) != 1 or files[0].stat().st_size > self._max_bytes:
            return signals  # нет файла — это сигнал общей проверки; несколько файлов — не разбираем
        header, rows = self._read(files[0])
        if not header:
            return signals
        declared = self._declared(task.instruction)
        q = question.lower()
        # в первой строке нет ни одного объявленного столбца — это строка данных, а не шапка: CSV без заголовка
        # решается по позициям из условия, отсутствие столбца не доказано
        headerless = bool(declared) and not declared & set(header)
        for col, triggers in self._triggers.items():
            if col in header or not any(t in q for t in triggers):
                continue
            # жёстко только если инструкция сама обещает этот столбец, а в файле его нет
            severity = HARD if col in declared and not headerless else SOFT
            signals.append(Signal("missing_column", severity, f"{files[0].name} has no column {col!r}"))
        # столбец обещан условием и назван в вопросе, а в шапке его нет — данных для ответа нет
        if not headerless:
            for col in sorted(declared - set(header) - set(self._triggers)):
                if mentions(q, col):
                    signals.append(Signal("missing_column", HARD, f"{files[0].name} has no declared column {col!r}"))
        for col, rx in self._filters.items():
            m = rx.search(question)
            if m and col in header:
                values = {r[header.index(col)].strip().lower() for r in rows if len(r) > header.index(col)}
                if m.group(1).strip().lower() not in values:
                    signals.append(Signal("unknown_value", SOFT, f"{col} {m.group(1)!r} not in {files[0].name}"))
        return signals

    def _months(self, question: str) -> list[Signal]:
        m = self._months_re.search(question)
        if not m:
            return []
        a, b = int(m.group(1)), int(m.group(2))
        if not (1 <= a <= 12 and 1 <= b <= 12) or a > b:
            return [Signal("bad_months", SOFT, f"months {a}..{b}")]
        return []

    def _declared(self, instruction: str) -> set[str]:
        m = self._declared_re.search(instruction)
        return {c.strip().lower() for c in m.group(1).split(",")} if m else set()

    @staticmethod
    def _read(path: Path) -> tuple[list[str], list[list[str]]]:
        # utf-8-sig: BOM иначе прилипает к первому столбцу; разделитель — по первой строке («;» в .csv тоже бывает)
        with path.open(encoding="utf-8-sig", errors="replace", newline="") as fh:
            first = fh.readline()
            delim = "\t" if path.suffix == ".tsv" else ","
            if delim not in first:
                delim = max(";\t|", key=first.count) if any(c in first for c in ";\t|") else delim
            fh.seek(0)
            reader = csv.reader(fh, delimiter=delim)
            header = [h.strip().lower() for h in next(reader, [])]
            return header, list(reader)
