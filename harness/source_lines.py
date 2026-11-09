"""Строки исходника для вклейки: разбиение как у ast, фрагмент узла, сдвиг отступа без порчи строковых литералов."""
from __future__ import annotations

import ast
import io
import tokenize


def split_lines(text: str) -> list[str]:
    """Строки с концами только по \\n, \\r\\n, \\r — как считает ast; str.splitlines режет и по \\x0c, \\u2028 и др.,
    и диапазоны строк из ast съезжали бы."""
    return io.StringIO(text, newline="").readlines()


def start_line(node: ast.stmt) -> int:
    decos = getattr(node, "decorator_list", [])
    return min([node.lineno] + [d.lineno for d in decos])


def segment(lines: list[str], node: ast.stmt, nl: str) -> str:
    return nl.join(lines[start_line(node) - 1:node.end_lineno]) + nl


def reindent(text: str, old: int, new: int, nl: str) -> str:
    if old == new:
        return text
    pad = " " * new
    keep = _string_lines(text)
    out = []
    for no, line in enumerate(text.split(nl)):
        if not line.strip() or no in keep:
            out.append(line)
            continue
        cut = len(line) - len(line.lstrip(" \t"))
        out.append(pad + line[min(cut, old):])
    return nl.join(out)


def _string_lines(text: str) -> set[int]:
    """Номера строк (с 0) внутри многострочных строковых литералов: их отступ — часть значения строки."""
    inside: set[int] = set()
    fstarts: list[int] = []
    fstart, fend = getattr(tokenize, "FSTRING_START", -1), getattr(tokenize, "FSTRING_END", -1)
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.STRING:
                inside.update(range(tok.start[0], tok.end[0]))
            elif tok.type == fstart:
                fstarts.append(tok.start[0])
            elif tok.type == fend and fstarts:
                inside.update(range(fstarts.pop(), tok.end[0]))
    except (tokenize.TokenError, SyntaxError):
        return set()
    return inside
