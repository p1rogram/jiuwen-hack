"""Бесплатная локальная проверка результата по полю check задачи."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from harness.executor import RunResult, run_python
from harness.tasks import Task

# число целиком, с дробной частью: иначе из «8825.0» последним «целым» оказывается «0»
NUM_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
# mtime на Windows идёт по грубым часам и отстаёт от time.time() до тика таймера (~15,6 мс): файл этого запуска
# бывает «старше» started. Не больше ~0,1 с: между запусками скриптов всегда вызов LLM (≥ 1 с), и answer.txt
# прошлой попытки не должен попасть в допуск
MTIME_SKEW_SEC = 0.05
# подпись — только буквы и знаки (без цифр), поэтому «12:30» не трогаем; точка нужна для «answer.txt:»
LABEL_RE = re.compile(r"^[A-Za-z][A-Za-z _.'/()-]{0,60}:\s*(\S.*)$")

# виды провала — ключи SuccessModel: от вида зависит, какая ветка после провала вероятнее даст ответ
NO_CODE, SCRIPT_ERROR, TESTS_FAIL, BAD_FORMAT = "no_code", "script_error", "tests_fail", "bad_format"
FAILURE_KINDS = (NO_CODE, SCRIPT_ERROR, TESTS_FAIL, BAD_FORMAT)


@dataclass(frozen=True)
class Verdict:
    ok: bool
    answer: str
    problem: str = ""
    kind: str = ""  # вид провала из FAILURE_KINDS; у успеха пусто


def last_line(text: str) -> str:
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    return lines[-1] if lines else ""


class Verifier:
    def __init__(self, error_tail_chars: int):
        self._tail = error_tail_chars

    async def check(self, task: Task, run: RunResult) -> Verdict:
        if run.timed_out:
            return Verdict(False, "", "script timed out", SCRIPT_ERROR)
        if run.returncode != 0:
            return Verdict(False, "", f"script failed (exit {run.returncode}):\n{run.stderr[-self._tail:]}",
                           SCRIPT_ERROR)
        kind = task.check
        if kind == "unittest":
            return await self._check_tests(task.workspace)
        # модель иногда только пишет answer.txt, ничего не печатая: ответ есть, повтор не нужен
        line = last_line(run.stdout) or self._answer_file(task, run.started)
        if kind == "exact_int":
            # берём последнее число строки: «After split into 3: 389» → 389
            nums = NUM_RE.findall(line)
            if not nums:
                return Verdict(False, line, f"last stdout line is not an integer: {line!r}", BAD_FORMAT)
            whole, _, frac = nums[-1].replace(",", "").partition(".")
            if frac.strip("0"):
                return Verdict(False, nums[-1], f"expected an integer, got {nums[-1]!r}", BAD_FORMAT)
            return Verdict(True, whole)
        if not line:
            return Verdict(False, "", "script printed nothing", BAD_FORMAT)
        # «Headquarters: Osaka» → «Osaka»; подпись — только буквы, поэтому «12:30» не трогаем
        m = LABEL_RE.match(line)
        return Verdict(True, m.group(1).strip() if m else line)

    @staticmethod
    def _answer_file(task: Task, started: float) -> str:
        """Последняя строка answer.txt, записанного этим запуском (не раньше started) — иначе пусто."""
        for folder in dict.fromkeys([task.workspace, task.task_dir]):
            path = folder / "answer.txt"
            try:
                if started and not path.is_symlink() and path.stat().st_mtime >= started - MTIME_SKEW_SEC:
                    return last_line(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
        return ""

    async def _check_tests(self, ws: Path) -> Verdict:
        tests = sorted(p.name for p in ws.glob("test_*.py"))
        if not tests:
            return Verdict(True, "DONE")
        for name in tests:
            res = await run_python([name], ws, 120)
            if not res.ok:
                return Verdict(False, "DONE", f"`python3 {name}` still fails:\n{(res.stdout + res.stderr)[-self._tail:]}",
                               TESTS_FAIL)
        return Verdict(True, "DONE")
