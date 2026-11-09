"""Вклейка исходника правки в исходник файла через ast: замена одноимённых определений диапазонами строк (без LLM)."""
from __future__ import annotations

import ast

from harness.source_lines import reindent as _reindent, segment as _segment, split_lines, start_line as _start

DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)
IMPORTS = (ast.Import, ast.ImportFrom)


class PatchError(ValueError):
    """Правку нельзя применить; текст уходит модели в повтор."""


def merge_source(original: str, patch: str) -> str:
    """Функции/классы патча заменяют одноимённые (среди нескольких — с теми же декораторами, иначе последнее),
    новые — в конец, но перед `if __name__`; класс — по членам, изменённый заголовок класса переносится;
    одиночный метод (self/cls или декоратор метода) — в единственный класс, где он есть; импорт — если его нет;
    присваивание имени заменяет такое же или встаёт после импортов. Остальные инструкции патча не переносятся."""
    orig_tree = parse_source(original, "the original file")
    patch_tree = parse_source(patch, "your code")
    nl = "\r\n" if "\r\n" in original else "\n"
    lines = split_lines(original)
    if lines and not lines[-1].endswith(("\n", "\r")):
        lines[-1] += nl
    patch_lines = [line.rstrip("\r\n") for line in split_lines(patch)]
    named: dict[str, list[ast.stmt]] = {}
    have_imports = set()
    body = orig_tree.body
    # новые импорты и присваивания — после последнего импорта, иначе после docstring модуля, иначе в начало
    imports_end = body[0].end_lineno if body and isinstance(body[0], ast.Expr) \
        and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str) else 0
    for node in body:
        name = _name(node)
        if name:
            named.setdefault(name, []).append(node)
        if isinstance(node, IMPORTS):
            imports_end = node.end_lineno
            have_imports.add(ast.dump(node))
    main_start = _start(body[-1]) if body and _is_main_guard(body[-1]) else None

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
        old = _pick(named.get(name, []), node, "the file")
        # метод с тем же именем, что у функции модуля, — правка метода, а не функции
        owner = _method_owner(orig_tree, name) if _method_like(node) and (
            old is None or isinstance(old, FUNCS) and not _method_like(old)) else None
        if owner is not None:
            target = _pick([n for n in owner.body if _name(n) == name and isinstance(n, DEFS)], node,
                           f"class {owner.name}")
            edits.replace[_start(target)] = (target.end_lineno,
                                             _reindent(text, node.col_offset, target.col_offset, nl))
        elif isinstance(old, ast.ClassDef) and isinstance(node, ast.ClassDef):
            _merge_members(old, node, patch_lines, nl, edits)
        elif old is not None:
            edits.replace[_start(old)] = (old.end_lineno, text)
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
        if tail and i == main_start:
            # новые функции — до блока запуска: иначе вызов из него упадёт с NameError
            out.extend(text + nl + nl for text in tail)
            tail = []
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
    _merge_header(old, new, patch_lines, nl, edits)
    members: dict[str, list[ast.stmt]] = {}
    for node in old.body:
        if name := _name(node):
            members.setdefault(name, []).append(node)
    indent = old.body[0].col_offset
    for node in new.body:
        name = _name(node)
        if not name:
            continue
        prev = _pick(members.get(name, []), node, f"class {old.name}")
        if isinstance(prev, ast.ClassDef) and isinstance(node, ast.ClassDef):
            _merge_members(prev, node, patch_lines, nl, edits)
            continue
        text = _segment(patch_lines, node, nl)
        if prev is not None:
            edits.replace[_start(prev)] = (prev.end_lineno, _reindent(text, node.col_offset, prev.col_offset, nl))
        else:
            edits.after.setdefault(old.end_lineno, []).append(nl + _reindent(text, node.col_offset, indent, nl))


def _merge_header(old: ast.ClassDef, new: ast.ClassDef, patch_lines: list[str], nl: str, edits: _Edits) -> None:
    """Базы, keywords и декораторы класса правки заменяют прежние: вклейка по членам их бы молча потеряла.
    Заголовок — строки от декораторов до первого члена (комментарии между ними заменяются вместе с ним).
    Голый `class A:` правки — заглушка частичного класса, а не удаление баз: заголовок не трогаем."""
    if _header_key(old) == _header_key(new) or not _covers(new, old):
        return
    old_end, new_end = _start(old.body[0]) - 1, _start(new.body[0]) - 1
    if old_end < old.lineno or new_end < new.lineno:
        raise PatchError(f"class {old.name!r}: header changed in a one-line class; "
                         f"send the class with its members on separate lines")
    text = _reindent(nl.join(patch_lines[_start(new) - 1:new_end]) + nl, new.col_offset, old.col_offset, nl)
    edits.replace[_start(old)] = (old_end, text)


def _covers(new: ast.ClassDef, old: ast.ClassDef) -> bool:
    """Заголовок правки задаёт всё, что было в прежнем: базы/keywords и декораторы (что не прислано — не стираем)."""
    return (bool(new.bases or new.keywords) or not (old.bases or old.keywords)) \
        and (bool(new.decorator_list) or not old.decorator_list)


def _header_key(node: ast.ClassDef) -> tuple:
    return tuple(ast.dump(n) for n in node.bases), tuple(ast.dump(n) for n in node.keywords), _decorators(node)


def _decorators(node: ast.stmt) -> tuple[str, ...]:
    return tuple(ast.dump(d) for d in getattr(node, "decorator_list", []))


def _pick(candidates: list[ast.stmt], node: ast.stmt, where: str) -> ast.stmt | None:
    """Из одноимённых определений (property и setter, overload, переопределение) — последнее с теми же
    декораторами; одно определение — оно же. Без совпадения среди нескольких def — отказ:
    иначе правка геттера затёрла бы сеттер."""
    if len(candidates) <= 1:
        return candidates[0] if candidates else None
    same = [c for c in candidates if _decorators(c) == _decorators(node)]
    if same:
        return same[-1]
    if not any(isinstance(c, DEFS) for c in candidates):
        return candidates[-1]
    raise PatchError(f"{_name(node)!r} is defined several times in {where} with different decorators; "
                     f"keep the decorators of the definition you fix")


def _is_main_guard(node: ast.stmt) -> bool:
    test = node.test if isinstance(node, ast.If) else None
    return isinstance(test, ast.Compare) and isinstance(test.left, ast.Name) and test.left.id == "__name__"


def _method_like(node: ast.stmt) -> bool:
    """Одиночная функция похожа на метод: первый аргумент self/cls или декоратор метода
    (иначе новая функция с именем чужого метода заменила бы метод)."""
    if not isinstance(node, FUNCS):
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


def method_names(tree: ast.Module) -> set[str]:
    """Имена методов всех классов модуля (для блока правки без заголовка)."""
    return {_name(m) for n in ast.walk(tree) if isinstance(n, ast.ClassDef) for m in n.body
            if isinstance(m, DEFS)}


def parse_source(source: str, what: str) -> ast.Module:
    try:
        return ast.parse(source)
    except (SyntaxError, ValueError) as exc:  # ValueError — NUL-байт в исходнике
        line = getattr(exc, "lineno", None)
        raise PatchError(f"syntax error in {what}, line {line}: {getattr(exc, 'msg', exc)}") from None


def _name(node: ast.stmt) -> str | None:
    if isinstance(node, DEFS):
        return node.name
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if len(targets) == 1 and isinstance(targets[0], ast.Name):
            return targets[0].id
    return None
