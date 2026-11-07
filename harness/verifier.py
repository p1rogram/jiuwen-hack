"""Бесплатная локальная проверка результата по полю check задачи."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from harness.executor import RunResult, run_python
from harness.tasks import Task

INT_RE = re.compile(r"-?\d[\d,]*")
LABEL_RE = re.compile(r"^[A-Za-z][A-Za-z _-]{0,40}:\s*(\S.*)$")


@dataclass(frozen=True)
class Verdict:
    ok: bool
    answer: str
    problem: str = ""


def last_line(text: str) -> str:
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    return lines[-1] if lines else ""


class Verifier:
    def __init__(self, error_tail_chars: int):
        self._tail = error_tail_chars

    async def check(self, task: Task, run: RunResult) -> Verdict:
        if run.timed_out:
            return Verdict(False, "", "script timed out")
        if run.returncode != 0:
            return Verdict(False, "", f"script failed (exit {run.returncode}):\n{run.stderr[-self._tail:]}")
        line = last_line(run.stdout)
        kind = task.check
        if kind == "unittest":
            return await self._check_tests(task.workspace)
        if kind == "exact_int":
            # берём последнее число строки: «After split into 3: 389» → 389
            nums = INT_RE.findall(line)
            if not nums:
                return Verdict(False, line, f"last stdout line is not an integer: {line!r}")
            return Verdict(True, nums[-1].replace(",", ""))
        if not line:
            return Verdict(False, "", "script printed nothing")
        # «Headquarters: Osaka» → «Osaka»; подпись — только буквы, поэтому «12:30» не трогаем
        m = LABEL_RE.match(line)
        return Verdict(True, m.group(1).strip() if m else line)

    async def _check_tests(self, ws: Path) -> Verdict:
        tests = sorted(p.name for p in ws.glob("test_*.py"))
        if not tests:
            return Verdict(True, "DONE")
        for name in tests:
            res = await run_python([name], ws, 120)
            if not res.ok:
                return Verdict(False, "DONE", f"`python3 {name}` still fails:\n{(res.stdout + res.stderr)[-self._tail:]}")
        return Verdict(True, "DONE")
