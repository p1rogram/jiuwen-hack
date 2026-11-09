"""Сжатие вывода unittest для промпта: из каждого провала — имя теста, кадры одной строкой и первая строка исключения."""
from __future__ import annotations

import re

SEP_RE = re.compile(r"^(=|-){20,}$")
HEAD_RE = re.compile(r"^(FAIL|ERROR): (\w+)")
FRAME_RE = re.compile(r'^\s*File "(?:.*[/\\])?([^/\\"]+)", line (\d+), in (\S+)')
MARKS_RE = re.compile(r"^\s*[~^]+\s*$")


def compact_unittest(text: str) -> str:
    """Разделители, «Traceback», пути, строки ~~^^ и подробности diff — шум: модели хватает строки проверки и
    ошибки, а без шума в лимит вывода помещаются все провалы. Не вывод unittest с провалами — без изменений."""
    lines = text.splitlines()
    if not any(HEAD_RE.match(line) for line in lines):
        return text
    out: list[str] = []
    i = 0
    while i < len(lines):
        head = HEAD_RE.match(lines[i])
        if head is None:
            if lines[i].startswith(("Ran ", "FAILED", "OK")):
                out.append(lines[i])
            i += 1
            continue
        out.append(f"{head.group(1)}: {head.group(2)}")
        i += 1
        while i < len(lines) and not HEAD_RE.match(lines[i]) and not (SEP_RE.match(lines[i]) and _block_end(lines, i)):
            frame = FRAME_RE.match(lines[i])
            if frame:
                src = lines[i + 1].strip() if i + 1 < len(lines) and not FRAME_RE.match(lines[i + 1]) else ""
                out.append(f"  {frame.group(1)}:{frame.group(2)} in {frame.group(3)}: {src}".rstrip(": "))
                i += 2 if src else 1
                continue
            line = lines[i]
            if line and not line.startswith((" ", "Traceback")) and not SEP_RE.match(line) and not MARKS_RE.match(line):
                out.append("  " + line)
                i = _skip_details(lines, i + 1)
                continue
            i += 1
    return "\n".join(out) + "\n"


def _block_end(lines: list[str], i: int) -> bool:
    """Разделитель `====` перед следующим провалом или `----` перед итогом «Ran N tests»."""
    nxt = lines[i + 1] if i + 1 < len(lines) else ""
    return bool(HEAD_RE.match(nxt)) or nxt.startswith("Ran ")


def _skip_details(lines: list[str], i: int) -> int:
    """После строки исключения — подробности diff до разделителя блока."""
    while i < len(lines) and not SEP_RE.match(lines[i]) and not HEAD_RE.match(lines[i]):
        i += 1
    return i
