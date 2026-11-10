"""Набор вариантов: новые формулировки и виды вопросов тех же 4 категорий (финал — «абсолютно новые вопросы»).

Запуск из корня репо: python -m tools.gen.variants D:/claude-tmp/jiuwen-runs/var1 [--seed 1]
Пишет <out>/<id>/task.json (+ workspace/); эталоны — <out>-expected.json, скрытые тесты coding —
<out>-hidden/<id>/test_hidden.py (вне папки задач: харнесс их не видит). Оценка — tools/gen/score_variants.py.
"""
from __future__ import annotations

import argparse
import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tools.gen import coding, variants_data, variants_search
from tools.gen.content import variants_coding as vcode
from tools.gen.content import variants_pool as pool
from tools.gen.content.coding_pool import POOL

SEARCH_KINDS = list(pool.SEARCH_Q) + ["head_of_employer", "employer_city", "mentor_born"]


@dataclass
class Variant:
    task_type: str
    instruction: str
    check: str
    expected: str | None               # None — задача невыполнима
    files: dict[str, str]
    reward: int
    hidden: str | None = None          # скрытый тест coding
    reference: dict[str, str] = field(default_factory=dict)  # эталонные файлы coding (только для тестов)
    task_id: str = ""

    @property
    def feasible(self) -> bool:
        return self.expected is not None


def pick(rng: random.Random, spec: list) -> Any:
    """[lo, hi] — целое из диапазона; список другой длины — один из значений."""
    return rng.randint(spec[0], spec[1]) if len(spec) == 2 else rng.choice(spec)


def math_params(rng: random.Random, fam: dict[str, Any]) -> dict[str, Any]:
    for _ in range(10_000):
        vals = {k: pick(rng, v) for k, v in fam["params"].items()}
        if eval(fam.get("where", "True"), dict(vals)):  # noqa: S307 — выражения из своего контента
            return vals
    raise ValueError(f"no params satisfy {fam['where']}")


def math_tasks(rng: random.Random) -> list[Variant]:
    out = []
    for fam in pool.MATH:
        vals = math_params(rng, fam)
        answer = eval(fam["answer"], dict(vals))  # noqa: S307 — значения как глобальные: их видит и lambda
        tail = "" if fam.get("no_tail") else rng.choice(pool.TAILS)
        out.append(Variant("math", fam["text"].format(**vals) + tail, "exact_int", str(answer), {}, rng.randint(1, 5)))
    for fam in pool.MATH_INFEASIBLE:
        vals = {k: pick(rng, v) for k, v in fam["params"].items()}
        out.append(Variant("math", fam["text"].format(**vals) + pool.TAILS[0], "exact_int", None, {}, rng.randint(1, 5)))
    return out


def data_tasks(rng: random.Random) -> list[Variant]:
    out = []
    for kind in pool.DATA_Q:
        text, files, answer, check = variants_data.feasible(rng, kind)
        out.append(Variant("data_analysis", text + rng.choice(pool.TAILS), check, answer, files, rng.randint(1, 5)))
    for spec in pool.DATA_INFEASIBLE:
        text, files = variants_data.infeasible(rng, spec)
        out.append(Variant("data_analysis", text + pool.TAILS[0], "exact_int", None, files, rng.randint(1, 5)))
    return out


def search_tasks(rng: random.Random) -> list[Variant]:
    out = []
    for kind in SEARCH_KINDS:
        text, files, answer = variants_search.feasible(rng, kind)
        out.append(Variant("search", text + rng.choice(pool.TAILS), "exact_str", answer, files, rng.randint(1, 5)))
    for template in pool.SEARCH_INFEASIBLE:
        text, files = variants_search.infeasible(rng, template)
        out.append(Variant("search", text + pool.TAILS[0], "exact_str", None, files, rng.randint(1, 5)))
    return out


def render_module_tests(mod: str, cases: list[tuple[str, tuple, Any]], method_per_case: bool) -> str:
    """Тест-файл `import <mod> as m`: метод на функцию (видимые) или все проверки в одном методе (скрытые)."""
    if method_per_case:
        methods = [(f"test_{name}", [coding.assert_line(name, a, e)]) for name, a, e in cases]
    else:
        methods = [("test_hidden", [coding.assert_line(name, a, e) for name, a, e in cases])]
    return coding.render_tests(methods).replace("import mylib as m", f"import {mod} as m")


def generated_coding(rng: random.Random, mod: str) -> Variant:
    funcs = rng.sample(POOL, rng.randint(3, 5))
    bugged = {f["name"]: rng.choice(f["bugs"]) for f in rng.sample(funcs, rng.randint(1, 2))}
    lib = "\n\n\n".join(bugged.get(f["name"], f["source"]) for f in funcs) + "\n"
    ref = "\n\n\n".join(f["source"] for f in funcs) + "\n"
    visible = [(f["name"], *(coding.failing_case(f, bugged[f["name"]]) if f["name"] in bugged else f["cases"][0]))
               for f in funcs]
    hidden = [(f["name"], a, e) for f in funcs for a, e in f["cases"]]
    test = f"test_{mod}.py"
    text = rng.choice(vcode.CODING_INSTRUCTIONS).format(test=test, mod=mod) + pool.CODE_TAIL
    return Variant("coding", text, "unittest", "tests_pass",
                   {f"{mod}.py": lib, test: render_module_tests(mod, visible, True)}, rng.randint(1, 5),
                   hidden=render_module_tests(mod, hidden, False), reference={f"{mod}.py": ref})


def coding_tasks(rng: random.Random) -> list[Variant]:
    out = [Variant("coding", t["instruction"] + pool.CODE_TAIL, "unittest", "tests_pass", dict(t["files"]),
                   rng.randint(1, 5), hidden=t["hidden"], reference=dict(t["reference"])) for t in vcode.CODING_FIXED]
    out += [generated_coding(rng, mod) for mod in vcode.CODING_MODULES]
    out += [Variant("coding", t["instruction"] + pool.CODE_TAIL, "unittest", None, dict(t["files"]), rng.randint(1, 5))
            for t in vcode.CODING_INFEASIBLE]
    return out


def build(seed: int) -> list[Variant]:
    rng = random.Random(seed)
    tasks = []
    for make in (math_tasks, data_tasks, search_tasks, coding_tasks):
        group = make(rng)
        rng.shuffle(group)  # невыполнимые не всегда в конце номеров
        for i, t in enumerate(group, 1):
            t.task_id = f"{t.task_type}_v{i:02d}"
        tasks += group
    return tasks


def write(out: Path, tasks: list[Variant]) -> None:
    hidden_root = out.parent / f"{out.name}-hidden"
    out.mkdir(parents=True)
    expected = {}
    for t in tasks:
        d = out / t.task_id
        d.mkdir()
        for rel, body in t.files.items():
            path = d / "workspace" / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body.encode("utf-8"))
        (d / "task.json").write_bytes(json.dumps({"task_id": t.task_id, "task_type": t.task_type,
                                                  "instruction": t.instruction, "reward": t.reward,
                                                  "check": t.check}, indent=2).encode("utf-8"))
        expected[t.task_id] = {"expected": t.expected, "feasible": t.feasible, "type": t.task_type}
        if t.hidden:
            (hidden_root / t.task_id).mkdir(parents=True, exist_ok=True)
            (hidden_root / t.task_id / "test_hidden.py").write_bytes(t.hidden.encode("utf-8"))
    (out.parent / f"{out.name}-expected.json").write_bytes(json.dumps(expected, indent=2).encode("utf-8"))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("out", type=Path)
    p.add_argument("--seed", type=int, default=1)
    args = p.parse_args()
    tasks = build(args.seed)
    write(args.out, tasks)
    print(f"{len(tasks)} tasks, {sum(t.feasible for t in tasks)} feasible -> {args.out}")


if __name__ == "__main__":
    main()
