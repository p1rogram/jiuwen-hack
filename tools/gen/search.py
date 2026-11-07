"""Генератор search-задач: kb/ из markdown-карточек людей и компаний, вопросы в 1–3 хопа."""
from __future__ import annotations

import random

from tools.gen.common import GenTask, templates, vocab

KINDS = {
    "missing_kb": "hard",       # папки kb/ нет
    "unknown_entity": "hard",   # человека из вопроса нет в kb
    "missing_field": "hard",    # у нужной карточки нет поля, у остальных есть
    "broken_chain": "hard",     # работодатель/наставник указан, а его карточки нет
}
BASE_Q = ["born", "employer_hq"]
HARD_Q = ["employer_founded", "mentor_born", "mentor_employer_hq"]
# вопрос → цепочка (отношения по порядку, затем атрибут)
CHAINS = {
    "born": ([], "Born"),
    "employer_hq": (["Employer"], "Headquarters"),
    "employer_founded": (["Employer"], "Founded"),
    "mentor_born": (["Mentor"], "Born"),
    "mentor_employer_hq": (["Mentor", "Employer"], "Headquarters"),
}


def slug(name: str) -> str:
    return name.lower().replace(" ", "_")


def make(rng: random.Random, variant: str, kind: str | None = None) -> GenTask:
    v, t = vocab(), templates()["search"]
    big = variant == "hard" or (kind is not None and rng.random() < 0.5)
    all_names = [f"{f} {l}" for f in v["first_names"] for l in v["last_names"]]
    people = rng.sample(all_names, rng.randint(80, 120) if big else rng.randint(36, 38))
    companies = rng.sample(v["companies"], rng.randint(16, 24) if big else 10)
    cards: dict[str, dict[str, str]] = {}
    for c in companies:
        cards[c] = {"kind": "company", "Headquarters": rng.choice(v["cities"]), "Founded": str(rng.randint(1950, 2015))}
    for p in people:
        cards[p] = {"kind": "person", "Born": str(rng.randint(1950, 2000)), "Employer": rng.choice(companies),
                    "Mentor": rng.choice([x for x in people if x != p])}

    q = rng.choice(HARD_Q if big else BASE_Q)
    if kind == "broken_chain" and q == "born":
        q = "employer_hq"
    target = rng.choice(people)
    relations, attr = CHAINS[q]
    if kind == "unknown_entity":
        target = rng.choice([n for n in all_names if n not in cards])
    elif kind == "broken_chain":
        # ломаем звено именно той цепочки, о которой спрашивают (не у target, если хоп не первый)
        hop = rng.randrange(len(relations))
        holder = target
        for rel in relations[:hop]:
            holder = cards[holder][rel]
        rel = relations[hop]
        phantom = (rng.choice(v["phantom_companies"]) if rel == "Employer"
                   else rng.choice([n for n in all_names if n not in cards]))
        cards[holder][rel] = phantom

    expected = None
    if kind is None or kind == "missing_field":
        cur = target
        for rel in relations:
            cur = cards[cur][rel]
        if kind == "missing_field":
            del cards[cur][attr]
        else:
            expected = cards[cur][attr]

    files = {}
    if kind != "missing_kb":
        for name, card in cards.items():
            fields = {k: val for k, val in card.items() if k != "kind"}
            lines = [f"# {name}", ""] + [f"{k}: {val}" for k, val in fields.items()]
            files[f"kb/{card['kind']}_{slug(name)}.md"] = "\n".join(lines) + "\n"
    instruction = " ".join([t["intro"], t[q].format(p=target), t["tool"], templates()["answer_suffix"]])
    task = GenTask("search", instruction, "exact_str", expected, files, variant)
    if kind is not None:
        task.kind, task.severity, task.expected = kind, KINDS[kind], None
    return task
