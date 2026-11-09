"""Ответ-правка: блоки кода с `# file: <путь>` и изменёнными функциями вклеиваются в файлы задачи через ast (без LLM)."""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from harness.executor import RunResult
from harness.merge import DEFS, IMPORTS, PatchError, merge_source, method_names, parse_source

__all__ = ["PatchApplier", "PatchBlock", "PatchError", "find_patch", "guess_blocks", "merge_source", "parse_blocks"]

CODE_RE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL)
FILE_RE = re.compile(r"#\s*file:\s*[`'\"]?([^`'\"\s]+)[`'\"]?\s*$", re.IGNORECASE)
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


def find_patch(text: str, workspace: Path) -> list[PatchBlock] | None:
    """Правка из ответа: блоки с заголовком, иначе последний блок без заголовка, если он однозначно правка модуля."""
    found = CODE_RE.findall(text)
    return parse_blocks(text) or guess_blocks(found[-1] if found else None, workspace)


def guess_blocks(code: str | None, workspace: Path) -> list[PatchBlock] | None:
    """Блок без заголовка: только функции/классы (и импорты), и все их имена есть в единственном модуле задачи
    (не test_*.py; методы классов — тоже) — это правка этого модуля. Как скрипт такой блок лишь определяет функции и файл не меняет
    (образ 2026-11-10, coding_0003: 50 токенов впустую, задачу дорешал этап 2)."""
    if not code:
        return None
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    names = {n.name for n in tree.body if isinstance(n, DEFS)}
    if not names or not all(isinstance(n, DEFS + IMPORTS) for n in tree.body):
        return None
    found = []
    for path in sorted(workspace.glob("*.py")):
        if path.name.startswith(("test_", "_")):
            continue
        try:
            module = ast.parse(path.read_bytes().decode("utf-8", "replace"))
        except SyntaxError:
            continue
        # методы классов тоже: одиночный метод вклеивается в свой класс (merge_source)
        defined = {n.name for n in module.body if isinstance(n, DEFS)} | method_names(module)
        if names <= defined:
            found.append(path.name)
    return [PatchBlock(found[0], code.strip("\n") + "\n")] if len(found) == 1 else None


def _header(line: str) -> re.Match | None:
    return FILE_RE.fullmatch(line.strip().strip("`*").strip())


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
                parse_source(block.source, "your code")
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
