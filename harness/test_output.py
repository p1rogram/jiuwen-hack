"""Сжатие вывода unittest для промпта: из каждого провала — заголовок, кадры одной строкой и исключения."""
from __future__ import annotations

import re

SEP_RE = re.compile(r"^(=|-){20,}$")
HEAD_RE = re.compile(r"^(FAIL|ERROR|UNEXPECTED SUCCESS): \S")
SHORT_HEAD_RE = re.compile(r"^([A-Z][A-Z ]*): (\S+) \([\w.]+\)(.*)$")
FRAME_RE = re.compile(r'^\s*File "(?:.*[/\\])?([^/\\"]+)", line (\d+)(?:, in (\S+))?')
MARKS_RE = re.compile(r"^\s*[~^]+\s*$")
CHAIN = ("During handling of the above exception", "The above exception was the direct cause")
DETAIL_LINES = 8
DETAIL_CHARS = 400


def compact_unittest(text: str) -> str:
    """Разделители, «Traceback», пути, строки ~~^^ — шум: модели хватает кадров и исключений, а без шума в лимит
    вывода помещаются все провалы. Вывод без блока провала unittest (заголовок + `----`) — без изменений."""
    lines = text.splitlines()
    if not any(HEAD_RE.match(line) and i + 1 < len(lines) and lines[i + 1].startswith("-" * 20)
               for i, line in enumerate(lines)):
        return text
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if HEAD_RE.match(line):
            end = _block_end(lines, i + 2)
            short = SHORT_HEAD_RE.match(line)
            out.append(f"{short.group(1)}: {short.group(2)}{short.group(3)}" if short else line)
            out.extend(_compact_block(lines[i + 1:end]))
            i = end
            continue
        if line.strip() and not SEP_RE.match(line):
            out.append(line)  # прогресс, печать тестов, итог Ran/FAILED
        i += 1
    return "\n".join(out) + "\n"


def _block_end(lines: list[str], i: int) -> int:
    """Конец блока провала: разделитель перед следующим заголовком или перед итогом «Ran N tests»."""
    while i < len(lines):
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if HEAD_RE.match(lines[i]) or (SEP_RE.match(lines[i]) and (HEAD_RE.match(nxt) or nxt.startswith("Ran "))):
            return i
        i += 1
    return i


def _compact_block(body: list[str]) -> list[str]:
    out: list[str] = []
    k = 0
    while k < len(body):
        line = body[k]
        frame = FRAME_RE.match(line)
        if frame:
            nxt = body[k + 1] if k + 1 < len(body) else ""
            src = nxt.strip() if nxt.startswith(" ") and not FRAME_RE.match(nxt) and not MARKS_RE.match(nxt) else ""
            where = f"{frame.group(1)}:{frame.group(2)}" + (f" in {frame.group(3)}" if frame.group(3) else "")
            out.append(f"  {where}: {src}" if src else f"  {where}")
            k += 2 if src else 1
            continue
        if not line.strip() or SEP_RE.match(line) or MARKS_RE.match(line) or line.startswith("Traceback"):
            k += 1
            continue
        if line.startswith(CHAIN):
            out.append("  " + line.strip())
            k += 1
            continue
        if line.startswith(" "):
            k += 1
            continue
        out.append("  " + line)  # строка исключения
        k += 1
        details = []
        while k < len(body) and not (FRAME_RE.match(body[k]) or body[k].startswith(("Traceback",) + CHAIN)
                                     or SEP_RE.match(body[k])):
            if body[k].strip():
                details.append(body[k])
            k += 1
        if not _self_explaining(line):
            out.extend(_capped(details))
    return out


def _self_explaining(line: str) -> bool:
    """«X != Y» без сокращений unittest уже показывает оба значения — подробности diff не нужны."""
    return " != " in line and "chars]" not in line and not line.rstrip().endswith("...")


def _capped(details: list[str]) -> list[str]:
    out, size = [], 0
    for line in details[:DETAIL_LINES]:
        size += len(line)
        if size > DETAIL_CHARS:
            break
        out.append("    " + line)
    return out
