"""coding: тесты компилируются и не противоречат сами себе (без запуска кода)."""
from __future__ import annotations

import ast
from typing import Any

from harness.feasibility.model import HARD, SOFT, Signal
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


def _skipped_nodes(tree: ast.AST) -> set[int]:
    """Узлы под декоратором метода (skip, expectedFailure, patch) и внутри with (mock.patch): там ожидание
    действует при другом состоянии или не проверяется вовсе — сравнивать его с остальными нельзя."""
    out: set[int] = set()
    for node in ast.walk(tree):
        decorated = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.decorator_list
        if decorated or isinstance(node, (ast.With, ast.AsyncWith)):
            out.update(id(n) for n in ast.walk(node))
    return out


def expectations(tree: ast.AST) -> list[tuple[str, str, Any, int]]:
    """(вызов, вид, ожидаемое значение, строка): вид eq — assertEqual(call, literal), bool — assertTrue/False(call)."""
    imported = imported_names(tree)
    skipped = _skipped_nodes(tree)
    out = []
    for node in ast.walk(tree):
        if id(node) in skipped or not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        name, args = node.func.attr, node.args
        if name in EQUAL_ASSERTS and len(args) >= 2:
            for call, lit in ((args[0], args[1]), (args[1], args[0])):
                if isinstance(call, ast.Call):
                    if pure_call(call, imported) and literal(lit):
                        out.append((ast.dump(call), "eq", ast.literal_eval(lit), node.lineno))
                    break
        elif name in BOOL_ASSERTS and args and pure_call(args[0], imported):
            out.append((ast.dump(args[0]), "bool", BOOL_ASSERTS[name], node.lineno))
    return out


def clash(a: tuple[str, Any], b: tuple[str, Any]) -> bool:
    """assertTrue проверяет истинность, а не равенство True: assertTrue(f(4)) и assertEqual(f(4), [0, 2]) совместимы."""
    (kind_a, va), (kind_b, vb) = a, b
    if kind_a == kind_b == "eq":
        return va != vb
    if kind_a == kind_b == "bool":
        return va is not vb
    return bool(va) is not bool(vb)


def patches_module(tree: ast.AST, imported: set[str]) -> bool:
    """mock/patch или присваивание атрибуту импортированного модуля: результат вызова зависит не только от аргументов."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in {"mock", "patch", "MagicMock"}:
            return True
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(
            node, (ast.AugAssign, ast.AnnAssign)) else []
        if any(isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id in imported
               for t in targets):
            return True
    return False


def stateful_functions(source: str) -> set[str]:
    """Функции модуля с состоянием: global/nonlocal или обращение к изменяемому объекту уровня модуля
    (список, словарь, множество) — у них один и тот же вызов вправе вернуть разное."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    mutable = {t.id for node in tree.body if isinstance(node, ast.Assign)
               and isinstance(node.value, (ast.List, ast.Dict, ast.Set, ast.Call))
               for t in node.targets if isinstance(t, ast.Name)}
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            inner = list(ast.walk(node))
            if any(isinstance(n, (ast.Global, ast.Nonlocal)) or (isinstance(n, ast.Name) and n.id in mutable)
                   for n in inner):
                out.add(node.name)
    return out


def called_name(key_call: ast.Call) -> str:
    func = key_call.func
    return func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""


class CodingCheck:
    def __init__(self, cfg: dict[str, Any] | None = None):
        pass

    def check(self, task: Task) -> list[Signal]:
        signals = []
        stateful = set()
        for lib in task.workspace.glob("*.py"):
            if not lib.name.startswith("test_"):
                stateful |= stateful_functions(lib.read_text(encoding="utf-8", errors="replace"))
        for test in sorted(task.workspace.glob("test_*.py")):
            source = test.read_text(encoding="utf-8", errors="replace")
            try:
                tree = ast.parse(source, filename=test.name)
            except SyntaxError as exc:
                signals.append(Signal("test_syntax_error", HARD, f"{test.name}:{exc.lineno}: {exc.msg}"))
                continue
            calls = {ast.dump(n): n for n in ast.walk(tree) if isinstance(n, ast.Call)}
            patched = patches_module(tree, imported_names(tree))
            seen: dict[str, tuple[str, Any, int]] = {}
            for key, kind, value, line in expectations(tree):
                if key in seen and clash(seen[key][:2], (kind, value)):
                    # доказательство только для чистой функции: с mock, присваиванием модулю или состоянием — спорно
                    severity = SOFT if patched or called_name(calls[key]) in stateful else HARD
                    signals.append(Signal("contradictory_tests", severity,
                                          f"{test.name}: lines {seen[key][2]} and {line} expect "
                                          f"{seen[key][1]!r} and {value!r} for the same call"))
                    break
                seen.setdefault(key, (kind, value, line))
        return signals
