"""Срез тест-файла для промпта: прошедшие тест-методы убираются, упавшие и всё остальное — как есть (без LLM)."""
from __future__ import annotations

import ast
import re

FAILED_RE = re.compile(r"^(?:FAIL|ERROR): (\w+) \(", re.MULTILINE)
RAN_RE = re.compile(r"^Ran (\d+) tests? in ", re.MULTILINE)


def failed_tests(raw: str) -> set[str] | None:
    """Имена упавших тестов из вывода unittest; None — вывод не unittest или упавших нет."""
    ran = RAN_RE.search(raw)
    failed = set(FAILED_RE.findall(raw))
    if not ran or not failed or len(failed) >= int(ran.group(1)):
        return None
    return failed


def drop_passing(source: str, failed: set[str], note: str) -> str:
    """Убирает методы `test*` классов верхнего уровня, которых нет среди упавших; на месте первого — строка `note`.
    Любое сомнение (не разбирается, упавший тест не найден среди методов, нечего убирать) — исходник как есть:
    имя вроде `setUpClass` или `test_mylib` (ошибка импорта) значит, что упал не метод и срез может скрыть причину."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source
    methods = [(cls, fn) for cls in tree.body if isinstance(cls, ast.ClassDef)
               for fn in cls.body if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))]
    if not failed <= {fn.name for _, fn in methods}:
        return source
    lines = source.splitlines(keepends=True)
    drop: dict[int, int] = {}  # первая строка (0-based) → конец (не включая)
    by_class: dict[ast.ClassDef, list[int]] = {}
    for cls, fn in methods:
        if not fn.name.startswith("test") or fn.name in failed:
            continue
        start = min([fn.lineno] + [d.lineno for d in fn.decorator_list]) - 1
        end = fn.end_lineno
        while end < len(lines) and not lines[end].strip():
            end += 1
        drop[start] = end
        by_class.setdefault(cls, []).append(start)
    if not drop:
        return source
    notes = {}
    for starts in by_class.values():
        first = min(starts)
        indent = lines[first][: len(lines[first]) - len(lines[first].lstrip())]
        notes[first] = f"{indent}{note.format(n=len(starts))}\n"
    out, i = [], 0
    while i < len(lines):
        if i in drop:
            out.append(notes.get(i, ""))
            i = drop[i]
        else:
            out.append(lines[i])
            i += 1
    return "".join(out)
