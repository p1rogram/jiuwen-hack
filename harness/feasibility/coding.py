"""coding: тесты компилируются и не противоречат сами себе (без запуска кода)."""
from __future__ import annotations

import ast
from typing import Any

from harness.feasibility.model import HARD, Signal
from harness.tasks import Task

EQUAL_ASSERTS = {"assertEqual", "assertEquals"}
BOOL_ASSERTS = {"assertTrue": True, "assertFalse": False}


def literal(node: ast.AST) -> bool:
    try:
        ast.literal_eval(node)
        return True
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return False


def imported_names(tree: ast.AST) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update((a.asname or a.name).split(".")[0] for a in node.names)
    return names


def pure_call(node: ast.AST, imported: set[str]) -> bool:
    """m.f(1, 2) или f(1, 2) из импорта, все аргументы — литералы. Не c.next() и не m.size(xs): у объекта и у
    изменяемого аргумента между вызовами меняется состояние, разные ожидания там не противоречие."""
    if not (isinstance(node, ast.Call) and node.args):
        return False
    func = node.func
    owner = func.value if isinstance(func, ast.Attribute) else func
    return (isinstance(owner, ast.Name) and owner.id in imported
            and all(literal(a) for a in node.args) and all(literal(k.value) for k in node.keywords))


def expectations(tree: ast.AST) -> list[tuple[str, Any, int]]:
    """(вызов, ожидаемое значение, строка) для assertEqual(call, literal) и assertTrue/False(call)."""
    imported = imported_names(tree)
    out = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        name, args = node.func.attr, node.args
        if name in EQUAL_ASSERTS and len(args) >= 2:
            for call, lit in ((args[0], args[1]), (args[1], args[0])):
                if isinstance(call, ast.Call):
                    if pure_call(call, imported) and literal(lit):
                        out.append((ast.dump(call), ast.literal_eval(lit), node.lineno))
                    break
        elif name in BOOL_ASSERTS and args and pure_call(args[0], imported):
            out.append((ast.dump(args[0]), BOOL_ASSERTS[name], node.lineno))
    return out


class CodingCheck:
    def __init__(self, cfg: dict[str, Any] | None = None):
        pass

    def check(self, task: Task) -> list[Signal]:
        signals = []
        for test in sorted(task.workspace.glob("test_*.py")):
            source = test.read_text(encoding="utf-8", errors="replace")
            try:
                tree = ast.parse(source, filename=test.name)
            except SyntaxError as exc:
                signals.append(Signal("test_syntax_error", HARD, f"{test.name}:{exc.lineno}: {exc.msg}"))
                continue
            seen: dict[str, tuple[Any, int]] = {}
            for key, value, line in expectations(tree):
                if key in seen and seen[key][0] != value:
                    signals.append(Signal("contradictory_tests", HARD,
                                          f"{test.name}: lines {seen[key][1]} and {line} expect "
                                          f"{seen[key][0]!r} and {value!r} for the same call"))
                    break
                seen.setdefault(key, (value, line))
        return signals
