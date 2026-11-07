"""coding: тесты компилируются и не противоречат сами себе (без запуска кода)."""
from __future__ import annotations

import ast
from typing import Any

from harness.feasibility.model import HARD, Signal
from harness.tasks import Task

EQUAL_ASSERTS = {"assertEqual", "assertEquals"}
BOOL_ASSERTS = {"assertTrue": True, "assertFalse": False}


def expectations(tree: ast.AST) -> list[tuple[str, Any, int]]:
    """(вызов, ожидаемое значение, строка) для assertEqual(call, literal) и assertTrue/False(call)."""
    out = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        name, args = node.func.attr, node.args
        if name in EQUAL_ASSERTS and len(args) >= 2:
            for call, lit in ((args[0], args[1]), (args[1], args[0])):
                if isinstance(call, ast.Call):
                    try:
                        out.append((ast.dump(call), ast.literal_eval(lit), node.lineno))
                    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
                        pass
                    break
        elif name in BOOL_ASSERTS and args and isinstance(args[0], ast.Call):
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
