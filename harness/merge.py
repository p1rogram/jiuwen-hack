"""Вклейка исходника правки в исходник файла через ast: замена одноимённых определений диапазонами строк (без LLM)."""
from __future__ import annotations

import ast

DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
IMPORTS = (ast.Import, ast.ImportFrom)


class PatchError(ValueError):
    """Правку нельзя применить; текст уходит модели в повтор."""


def merge_source(original: str, patch: str) -> str:
    """Функции/классы патча заменяют одноимённые (последнее определение), новые — в конец; класс — по членам;
    одиночный метод (self/cls или декоратор метода) — в единственный класс, где он есть; импорт — если его нет;
    присваивание имени заменяет такое же или встаёт после импортов. Остальные инструкции патча не переносятся."""
    orig_tree = parse_source(original, "the original file")
    patch_tree = parse_source(patch, "your code")
    nl = "\r\n" if "\r\n" in original else "\n"
    lines = original.splitlines(keepends=True)
    if lines and not lines[-1].endswith(("\n", "\r")):
        lines[-1] += nl
    patch_lines = patch.splitlines()
    named = {}
    have_imports = set()
    body = orig_tree.body
    # новые импорты и присваивания — после последнего импорта, иначе после docstring модуля, иначе в начало
    imports_end = body[0].end_lineno if body and isinstance(body[0], ast.Expr) \
        and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str) else 0
    for node in body:
        name = _name(node)
        if name:
            named[name] = node  # последнее определение — действующее
        if isinstance(node, IMPORTS):
            imports_end = node.end_lineno
            have_imports.add(ast.dump(node))

    edits = _Edits()
    head: list[str] = []
    tail: list[str] = []
    for node in patch_tree.body:
        text = _segment(patch_lines, node, nl)
        if isinstance(node, IMPORTS):
            if ast.dump(node) not in have_imports:
                have_imports.add(ast.dump(node))
                head.append(text)
            continue
        name = _name(node)
        if not name:
            continue
        old = named.get(name)
        if isinstance(old, ast.ClassDef) and isinstance(node, ast.ClassDef):
            _merge_members(old, node, patch_lines, nl, edits)
        elif old is not None:
            edits.replace[_start(old)] = (old.end_lineno, text)
        elif _method_like(node) and (owner := _method_owner(orig_tree, name)) is not None:
            old = [n for n in owner.body if _name(n) == name and isinstance(n, DEFS)][-1]
            edits.replace[_start(old)] = (old.end_lineno, _reindent(text, node.col_offset, old.col_offset, nl))
        elif isinstance(node, DEFS):
            tail.append(text)
        else:
            head.append(text)

    out: list[str] = []
    i = 1
    while i <= len(lines):
        if head and i > imports_end:
            out.extend(head)
            head = []
        if i in edits.replace:
            end, text = edits.replace[i]
            out.append(text)
        else:
            end = i
            out.append(lines[i - 1])
        out.extend(edits.after.get(end, []))
        i = end + 1
    out.extend(head)
    for text in tail:
        out.append(nl + nl + text if out and out[-1].strip() else text)
    result = "".join(out)
    parse_source(result, "the patched file")
    return result


class _Edits:
    """Правки исходника: замена диапазона строк (ключ — первая строка) и вставка после строки."""

    def __init__(self) -> None:
        self.replace: dict[int, tuple[int, str]] = {}
        self.after: dict[int, list[str]] = {}


def _merge_members(old: ast.ClassDef, new: ast.ClassDef, patch_lines: list[str], nl: str, edits: _Edits) -> None:
    """Класс правки вклеивается по членам: модель часто присылает класс только с изменённым методом,
    замена класса целиком стёрла бы остальные методы."""
    members = {}
    for node in old.body:
        if name := _name(node):
            members[name] = node
    indent = old.body[0].col_offset
    for node in new.body:
        name = _name(node)
        if not name:
            continue
        prev = members.get(name)
        if isinstance(prev, ast.ClassDef) and isinstance(node, ast.ClassDef):
            _merge_members(prev, node, patch_lines, nl, edits)
            continue
        text = _segment(patch_lines, node, nl)
        if prev is not None:
            edits.replace[_start(prev)] = (prev.end_lineno, _reindent(text, node.col_offset, prev.col_offset, nl))
        else:
            edits.after.setdefault(old.end_lineno, []).append(nl + _reindent(text, node.col_offset, indent, nl))


def _method_like(node: ast.stmt) -> bool:
    """Одиночная функция похожа на метод: первый аргумент self/cls или декоратор метода
    (иначе новая функция с именем чужого метода заменила бы метод)."""
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    args = node.args.posonlyargs + node.args.args
    if args and args[0].arg in ("self", "cls"):
        return True
    for deco in node.decorator_list:
        base = deco.func if isinstance(deco, ast.Call) else deco
        if isinstance(base, ast.Name) and base.id in ("staticmethod", "classmethod", "property"):
            return True
        if isinstance(base, ast.Attribute) and base.attr in ("setter", "getter", "deleter"):
            return True
    return False


def _method_owner(tree: ast.Module, name: str) -> ast.ClassDef | None:
    owners = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)
              and any(_name(m) == name and isinstance(m, DEFS) for m in n.body)]
    if len(owners) > 1:
        names = ", ".join(sorted({o.name for o in owners}))
        raise PatchError(f"method {name!r} is defined in several classes ({names}); "
                         f"send it inside its class: class <Name>: + the method")
    return owners[0] if owners else None


def _reindent(text: str, old: int, new: int, nl: str) -> str:
    if old == new:
        return text
    pad = " " * new
    out = []
    for line in text.split(nl):
        if not line.strip():
            out.append(line)
            continue
        cut = len(line) - len(line.lstrip(" \t"))
        out.append(pad + line[min(cut, old):])
    return nl.join(out)


def method_names(tree: ast.Module) -> set[str]:
    """Имена методов всех классов модуля (для блока правки без заголовка)."""
    return {_name(m) for n in ast.walk(tree) if isinstance(n, ast.ClassDef) for m in n.body
            if isinstance(m, DEFS)}


def parse_source(source: str, what: str) -> ast.Module:
    try:
        return ast.parse(source)
    except SyntaxError as exc:
        raise PatchError(f"syntax error in {what}, line {exc.lineno}: {exc.msg}") from None


def _name(node: ast.stmt) -> str | None:
    if isinstance(node, DEFS):
        return node.name
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if len(targets) == 1 and isinstance(targets[0], ast.Name):
            return targets[0].id
    return None


def _start(node: ast.stmt) -> int:
    decos = getattr(node, "decorator_list", [])
    return min([node.lineno] + [d.lineno for d in decos])


def _segment(lines: list[str], node: ast.stmt, nl: str) -> str:
    return nl.join(lines[_start(node) - 1:node.end_lineno]) + nl
