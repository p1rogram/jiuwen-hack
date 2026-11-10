"""Узкий срез базы знаний без LLM: файлы-семена по заголовку из вопроса и обход связей по упоминанию заголовков."""
from __future__ import annotations

import re
from pathlib import Path

HEADING_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


def _word_re(phrase: str) -> re.Pattern:
    return re.compile(rf"(?<!\w){re.escape(phrase)}(?!\w)")


def kb_slice(instruction: str, files: list[Path], depth: int, max_files: int) -> list[Path] | None:
    """Семена и достижимые от них файлы; None — ни один заголовок не упомянут в вопросе (сузить нечем).

    Связь — заголовок другой карточки, упомянутый в тексте файла. Не по строкам «Ключ: Значение»: разметка
    бывает любой («- Advisor: X», «**Employer:** X», таблица, фраза), и шаблон строки терял звенья цепочки."""
    bodies: dict[Path, str] = {}
    by_heading: dict[str, Path] = {}
    for p in files:
        body = p.read_text(encoding="utf-8", errors="replace")
        bodies[p] = body.lower()
        m = HEADING_RE.search(body)
        if m:
            by_heading.setdefault(m.group(1).lower(), p)
    patterns = {h: _word_re(h) for h in by_heading}
    text = instruction.lower()
    # длинные заголовки первыми: «Anna Lee» не должна уступить место короткому совпадению внутри другого имени
    seeds = [p for h, p in sorted(by_heading.items(), key=lambda hp: (-len(hp[0]), hp[0]))
             if patterns[h].search(text)]
    if not seeds:
        return None
    chosen = list(dict.fromkeys(seeds))
    frontier = chosen
    for _ in range(depth):
        nxt: list[Path] = []
        for p in frontier:
            hits = []
            for h, q in by_heading.items():
                if q is p or q in chosen or q in nxt:
                    continue
                m = patterns[h].search(bodies[p])
                if m:
                    hits.append((m.start(), q))
            # порядок упоминания в карточке: при обрезке по max_files остаются ближние звенья
            for _, q in sorted(hits, key=lambda hq: hq[0]):
                if q not in nxt:
                    nxt.append(q)
        chosen += nxt
        frontier = nxt
    return chosen[:max_files]
