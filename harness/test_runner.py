"""Изолированный запуск unittest с подтверждением ненулевого числа тестов."""
from __future__ import annotations

import uuid
from dataclasses import replace
from pathlib import Path

from harness.executor import RunResult, run_python


_PROGRAM = """
import sys, unittest
pattern, marker = sys.argv[1:]
# Discovery исполняет suite сам; main при импорте не должен запускать его второй раз.
unittest.main = lambda *args, **kwargs: None
suite = unittest.defaultTestLoader.discover('.', pattern=pattern)
result = unittest.TextTestRunner(verbosity=2).run(suite)
count = result.testsRun - len(result.skipped)
print(marker + str(count), file=sys.stderr)
sys.exit(0 if count > 0 and result.wasSuccessful() else 1)
"""


async def run_tests(workspace: Path, pattern: str, timeout_sec: float) -> RunResult:
    marker = f"__test_result_{uuid.uuid4().hex}__:"
    # -B запрещает запись, отдельный prefix исключает чтение старого кэша после быстрой правки.
    cache = str(workspace.resolve() / marker.replace(':', ''))
    result = await run_python(['-B', '-X', f'pycache_prefix={cache}', '-c', _PROGRAM, pattern, marker],
                              workspace, timeout_sec)
    lines = result.stderr.splitlines(keepends=True)
    count = None
    if lines and lines[-1].strip().startswith(marker):
        value = lines.pop().strip()[len(marker):]
        count = int(value) if value.isdecimal() else None
    stderr = ''.join(lines)
    if count is None or count == 0:
        stderr += '\nNo completed unittest tests were confirmed.\n'
    code = result.returncode or (1 if not count else 0)
    return replace(result, stderr=stderr, returncode=code)
