"""Узкий срез базы знаний без LLM: файлы-семена по заголовку из вопроса и обход связей «Ключ: Значение»."""
from __future__ import annotations

import re
from pathlib import Path

HEADING_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
FIELD_RE = re.compile(r"^[A-Za-z][\w ]*:\s*(.+?)\s*$", re.MULTILINE)


def kb_slice(instruction: str, files: list[Path], depth: int, max_files: int) -> list[Path] | None:
    """Семена и достижимые от них файлы; None — ни один заголовок не упомянут в вопросе (сузить нечем)."""
    bodies: dict[Path, str] = {}
    by_heading: dict[str, Path] = {}
    for p in files:
        body = p.read_text(encoding="utf-8", errors="replace")
        bodies[p] = body
        m = HEADING_RE.search(body)
        if m:
            by_heading.setdefault(m.group(1).lower(), p)
    text = instruction.lower()
    # длинные заголовки первыми: «Anna Lee» не должна уступить место короткому совпадению внутри другого имени
    seeds = [p for h, p in sorted(by_heading.items(), key=lambda hp: (-len(hp[0]), hp[0]))
             if re.search(rf"(?<!\w){re.escape(h)}(?!\w)", text)]
    if not seeds:
        return None
    chosen = list(dict.fromkeys(seeds))
    frontier = chosen
    for _ in range(depth):
        nxt = []
        for p in frontier:
            for value in FIELD_RE.findall(bodies[p]):
                q = by_heading.get(value.lower())
                if q is not None and q not in chosen and q not in nxt:
                    nxt.append(q)
        chosen += nxt
        frontier = nxt
    return chosen[:max_files]
