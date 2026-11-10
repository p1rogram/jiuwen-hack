"""search для набора вариантов: kb/ с разными названиями полей и формой строк, вопросы новых видов."""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

from tools.gen.common import vocab
from tools.gen.content import variants_pool as pool


@dataclass
class Kb:
    fields: dict[str, str]
    people: dict[str, dict[str, Any]]     # имя → born, employer, mentor (может не быть)
    companies: dict[str, dict[str, Any]]  # название → city, founded, head

    def files(self, bullets: bool) -> dict[str, str]:
        out = {}
        for name, card in {**self.people, **self.companies}.items():
            lines = [f"{'- ' if bullets else ''}{self.fields[k]}: {v}" for k, v in card.items()]
            out[f"kb/{name.lower().replace(' ', '_')}.md"] = f"# {name}\n\n" + "\n".join(lines) + "\n"
        return out

    def label(self, key: str) -> str:
        word = self.fields[key]
        return word if word.isupper() else word.lower()


def make_kb(rng: random.Random) -> Kb:
    v = vocab()
    names = rng.sample([f"{f} {l}" for f in v["first_names"] for l in v["last_names"]], rng.randint(8, 12))
    firms = rng.sample(v["companies"], rng.randint(3, 5))
    born = rng.sample(range(1950, 2001), len(names))
    founded = rng.sample(range(1950, 2016), len(firms))
    heads = rng.sample(names, len(firms))
    people = {}
    for i, n in enumerate(names):
        card: dict[str, Any] = {"born": born[i], "employer": rng.choice(firms)}
        if rng.random() < 0.85:
            card["mentor"] = rng.choice([x for x in names if x != n])
        people[n] = card
    companies = {c: {"city": rng.choice(v["cities"]), "founded": founded[i], "head": heads[i]}
                 for i, c in enumerate(firms)}
    return Kb(rng.choice(pool.KB_FIELDS), people, companies)


def ask(kind: str, rng: random.Random, kb: Kb) -> tuple[dict, Any]:
    p = rng.choice(sorted(kb.people))
    c = rng.choice(sorted(kb.companies))
    labels = {"head": kb.label("head"), "mentor": kb.label("mentor")}
    mentor = kb.people[p].get("mentor")
    if kind == "head_of_employer":
        return {"p": p, **labels}, kb.companies[kb.people[p]["employer"]]["head"]
    if kind == "mentor_born":
        return {"p": p, **labels}, mentor and kb.people[mentor]["born"]
    if kind == "employer_city":
        return {"p": p}, kb.companies[kb.people[p]["employer"]]["city"]
    if kind == "count_employees":
        return {"c": c}, sum(card["employer"] == c for card in kb.people.values())
    if kind == "born_first":
        q = rng.choice([x for x in sorted(kb.people) if x != p])
        return {"p": p, "q": q}, min((p, q), key=lambda n: kb.people[n]["born"])
    if kind == "oldest_company":
        return {}, min(kb.companies, key=lambda n: kb.companies[n]["founded"])
    if kind == "mentor_employer_city":
        return {"p": p, **labels}, mentor and kb.companies[kb.people[mentor]["employer"]]["city"]
    if kind == "mentor_of_head":
        return {"c": c, **labels}, kb.people[kb.companies[c]["head"]].get("mentor")
    if kind == "age_at_founding":
        age = kb.companies[c]["founded"] - kb.people[p]["born"]
        return {"p": p, "c": c}, age if age > 0 else None
    if kind == "youngest_person":
        return {}, max(kb.people, key=lambda n: kb.people[n]["born"])
    d = rng.choice([x for x in sorted(kb.companies) if x != c])
    gap = kb.companies[d]["founded"] - kb.companies[c]["founded"]
    return {"c": c, "d": d}, gap if gap > 0 else None


def instruction(rng: random.Random, question: str) -> str:
    return rng.choice(pool.KB_INTROS) + question + rng.choice([pool.KB_TOOL_HINT, ""])


def feasible(rng: random.Random, kind: str) -> tuple[str, dict[str, str], str]:
    while True:
        kb = make_kb(rng)
        params, answer = ask(kind, rng, kb)
        if answer is not None:
            break
    return instruction(rng, pool.SEARCH_Q[kind].format(**params)), kb.files(rng.random() < 0.34), str(answer)


def infeasible(rng: random.Random, template: str) -> tuple[str, dict[str, str]]:
    kb = make_kb(rng)
    v = vocab()
    phantom = rng.choice([f"{f} {l}" for f in v["first_names"] for l in v["last_names"] if f"{f} {l}" not in kb.people])
    return instruction(rng, template.format(phantom=phantom)), kb.files(rng.random() < 0.34)
