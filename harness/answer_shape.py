"""Структура ответа этапа 1 в числах (без LLM и без записи): проза вокруг блоков кода и какие функции правки
действительно изменены. Нужна для замера формы ответа; текст ответа в журнал не попадает."""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from harness.merge import DEFS
from harness.patching import CODE_RE, PatchBlock


def answer_shape(text: str, blocks: list[PatchBlock] | None, workspace: Path) -> dict[str, Any]:
    """Символы до первого и после последнего блока кода, число блоков; у правки — функции верхнего уровня
    (`defs`), изменённые / прежние (ast совпадает с исходником до вклейки) / новые, `whole_file` — в правке все
    функции модуля. Ошибка разбора — поле `error`, остальное, что успели посчитать."""
    spans = [m.span() for m in CODE_RE.finditer(text)]
    out: dict[str, Any] = {
        "before_chars": spans[0][0] if spans else len(text),
        "after_chars": len(text) - spans[-1][1] if spans else 0,
        "blocks": len(spans),
    }
    if not blocks:
        return out
    counts = {"defs": 0, "changed": 0, "unchanged": 0, "new": 0}
    whole = True
    try:
        for block in blocks:
            patch = ast.parse(block.source)
            original = _original(workspace / block.path)
            known = _bodies(original) if original else {}
            top = {n.name for n in original.body if isinstance(n, DEFS)} if original else set()
            names = set()
            for node in patch.body:
                if not isinstance(node, DEFS):
                    continue
                counts["defs"] += 1
                names.add(node.name)
                for name, dump in _units(node):
                    if name not in known:
                        counts["new"] += 1
                    elif dump in known[name]:
                        counts["unchanged"] += 1
                    else:
                        counts["changed"] += 1
            whole = whole and bool(top) and top <= names
    except (SyntaxError, OSError, ValueError) as exc:
        out.update(counts, error=f"{type(exc).__name__}: {exc}"[:200])
        return out
    out.update(counts, whole_file=whole)
    return out


def _original(path: Path) -> ast.Module | None:
    if not path.is_file():
        return None
    return ast.parse(path.read_bytes().decode("utf-8", "replace"))


def _units(node: ast.stmt) -> list[tuple[str, str]]:
    """Единицы сравнения: функция целиком, у класса — каждый его метод (класс правки часто неполный)."""
    if isinstance(node, ast.ClassDef):
        members = [m for m in node.body if isinstance(m, DEFS)]
        if members:
            return [u for m in members for u in _units(m)]
    return [(node.name, ast.dump(node))]


def _bodies(tree: ast.Module) -> dict[str, set[str]]:
    """Имя → ast всех функций модуля с этим именем, включая методы (одиночный метод правки ищется среди них)."""
    known: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            known.setdefault(node.name, set()).add(ast.dump(node))
        elif isinstance(node, ast.ClassDef) and not any(isinstance(m, DEFS) for m in node.body):
            known.setdefault(node.name, set()).add(ast.dump(node))
    return known
