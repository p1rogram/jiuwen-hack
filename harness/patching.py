"""Ответ-правка: блоки кода с `# file: <путь>` и изменёнными функциями вклеиваются в файлы задачи через ast (без LLM)."""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from harness.executor import RunResult

CODE_RE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL)
FILE_RE = re.compile(r"#\s*file:\s*[`'\"]?([^`'\"\s]+)[`'\"]?\s*$", re.IGNORECASE)
DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
IMPORTS = (ast.Import, ast.ImportFrom)


class PatchError(ValueError):
    """Правку нельзя применить; текст уходит модели в повтор."""


@dataclass(frozen=True)
class PatchBlock:
    path: str
    source: str


def parse_blocks(text: str) -> list[PatchBlock] | None:
    """Блоки с `# file: <путь>` первой непустой строкой внутри или последней непустой строкой перед блоком
    (модель часто пишет заголовок до ```python); ни одного — None (ответ-скрипт)."""
    blocks = []
    prev_end = 0
    for match in CODE_RE.finditer(text):
        lines = match.group(1).splitlines()
        first = next((i for i, ln in enumerate(lines) if ln.strip()), None)
        m = _header(lines[first]) if first is not None else None
        if m:
            lines = lines[first + 1:]
        else:
            before = [ln for ln in text[prev_end:match.start()].splitlines() if ln.strip()]
            m = _header(before[-1]) if before else None
        prev_end = match.end()
        if m:
            blocks.append(PatchBlock(m.group(1), "\n".join(lines).strip("\n") + "\n"))
    if not blocks and "```" not in text:
        # код без разметки: всё после строки-заголовка (проба 2026-11-10, coding_0008)
        lines = text.splitlines()
        at = next((i for i, ln in enumerate(lines) if _header(ln)), None)
        if at is not None:
            blocks.append(PatchBlock(_header(lines[at]).group(1), "\n".join(lines[at + 1:]).strip("\n") + "\n"))
    return blocks or None


def _header(line: str) -> re.Match | None:
    return FILE_RE.fullmatch(line.strip().strip("`*").strip())


def merge_source(original: str, patch: str) -> str:
    """Функции/классы патча заменяют одноимённые (последнее определение), новые — в конец; импорт — если его нет;
    присваивание имени заменяет такое же или встаёт после импортов. Остальные инструкции патча не переносятся."""
    orig_tree = _parse(original, "the original file")
    patch_tree = _parse(patch, "your code")
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

    replace: dict[int, tuple[int, str]] = {}  # начальная строка (1-based) → (конечная, новый текст)
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
        if old is not None:
            replace[_start(old)] = (old.end_lineno, text)
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
        if i in replace:
            end, text = replace[i]
            out.append(text)
            i = end + 1
        else:
            out.append(lines[i - 1])
            i += 1
    out.extend(head)
    for text in tail:
        out.append(nl + nl + text if out and out[-1].strip() else text)
    result = "".join(out)
    _parse(result, "the patched file")
    return result


class PatchApplier:
    """Вклейка блоков в папку задачи: все файлы или ни одного; итог — RunResult для Verifier."""

    def apply(self, blocks: list[PatchBlock], workspace: Path) -> RunResult:
        try:
            updates = self._plan(blocks, workspace)
        except PatchError as exc:
            return RunResult(1, "", f"patch rejected: {exc}", False)
        for path, data in updates.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            # байты, а не write_text: в Windows write_text меняет \n на \r\n
            path.write_bytes(data.encode("utf-8"))
        return RunResult(0, "", "", False)

    def _plan(self, blocks: list[PatchBlock], workspace: Path) -> dict[Path, str]:
        updates: dict[Path, str] = {}
        for block in blocks:
            path = self._target(block.path, workspace)
            if path in updates:
                current = updates[path]
            elif path.is_file():
                current = path.read_bytes().decode("utf-8", "replace")
            else:
                _parse(block.source, "your code")
                updates[path] = block.source
                continue
            updates[path] = merge_source(current, block.source)
        return updates

    @staticmethod
    def _target(raw: str, workspace: Path) -> Path:
        rel = PurePosixPath(raw.replace("\\", "/"))
        if rel.is_absolute() or ".." in rel.parts or re.match(r"^[A-Za-z]:", raw):
            raise PatchError(f"path {raw!r} is outside the task folder; use a path relative to it")
        if rel.name.startswith("test_"):
            raise PatchError(f"{raw!r} is a test file; do not edit tests")
        if rel.suffix != ".py":
            raise PatchError(f"{raw!r} is not a .py file")
        root = workspace.resolve()
        path = (root / rel).resolve()
        if root not in path.parents:
            raise PatchError(f"path {raw!r} is outside the task folder")
        return path


def _parse(source: str, what: str) -> ast.Module:
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
