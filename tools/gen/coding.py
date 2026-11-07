"""Генератор coding-задач: mylib.py с багами + test_mylib.py в формате примеров организаторов."""
from __future__ import annotations

import random
from typing import Any, Callable

from tools.gen.common import GenTask, templates
from tools.gen.content.coding_pool import POOL

# вид невыполнимости → ожидаемая тяжесть сигнала детектора
KINDS = {
    "missing_tests": "soft",        # тестов нет — проверить нечего; .py можно и написать, поэтому soft
    "missing_lib": "soft",          # mylib.py нет, но его можно написать по тестам
    "contradictory_tests": "hard",  # один вызов — два разных ожидаемых значения
    "broken_test_syntax": "hard",   # тесты не компилируются, а править их нельзя
}


def compile_func(source: str, name: str) -> Callable[..., Any]:
    ns: dict[str, Any] = {}
    exec(source, ns)  # noqa: S102 — исходники из своего пула, не из задачи
    return ns[name]


def failing_case(entry: dict[str, Any], bug_source: str) -> tuple:
    fn = compile_func(bug_source, entry["name"])
    for args, expected in entry["cases"]:
        try:
            if fn(*args) != expected:
                return args, expected
        except Exception:
            return args, expected
    raise ValueError(f"bug of {entry['name']} breaks no case")


def assert_line(name: str, args: tuple, expected: Any) -> str:
    return f"self.assertEqual(m.{name}({', '.join(repr(a) for a in args)}), {expected!r})"


def render_tests(methods: list[tuple[str, list[str]]]) -> str:
    lines = ["import unittest", "", "import mylib as m", "", "", "class T(unittest.TestCase):"]
    for i, (method, asserts) in enumerate(methods):
        if i:
            lines.append("")
        lines.append(f"    def {method}(self):")
        lines += [f"        {a}" for a in asserts]
    lines += ["", "", "if __name__ == '__main__':", "    unittest.main()", ""]
    return "\n".join(lines)


def other_value(value: Any) -> Any:
    if isinstance(value, bool):
        return not value
    if isinstance(value, (int, float)):
        return value + 1
    if isinstance(value, str):
        return value + "x"
    if isinstance(value, list):
        return value + [0]
    raise TypeError(type(value))


def make(rng: random.Random, variant: str, kind: str | None = None) -> GenTask:
    big = variant == "hard" or (kind is not None and rng.random() < 0.5)
    funcs = rng.sample(POOL, rng.randint(10, 14) if big else rng.randint(4, 6))
    bugged = {f["name"]: rng.choice(f["bugs"]) for f in rng.sample(funcs, rng.randint(4, 7) if big else rng.randint(2, 3))}
    lib = "\n\n\n".join(bugged.get(f["name"], f["source"]) for f in funcs) + "\n"
    ref = "\n\n\n".join(f["source"] for f in funcs) + "\n"

    order = funcs[:]
    rng.shuffle(order)
    methods = []
    for f in order:
        if big:
            cases = list(f["cases"])
        elif f["name"] in bugged:
            cases = [failing_case(f, bugged[f["name"]])]
        else:
            cases = [f["cases"][0]]
        methods.append((f"test_{f['name']}", [assert_line(f["name"], a, e) for a, e in cases]))

    files = {"mylib.py": lib, "test_mylib.py": render_tests(methods)}
    task = GenTask("coding", templates()["coding"], "unittest", "tests_pass", files, variant,
                   reference={"mylib.py": ref})
    if kind is None:
        return task
    task.kind, task.severity, task.expected = kind, KINDS[kind], None
    if kind == "missing_tests":
        del files["test_mylib.py"]
    elif kind == "missing_lib":
        del files["mylib.py"]
    elif kind == "contradictory_tests":
        f = rng.choice(funcs)
        args, expected = f["cases"][0]
        clash = (f"test_{f['name']}_extra", [assert_line(f["name"], args, other_value(expected))])
        methods.insert(rng.randint(0, len(methods)), clash)
        files["test_mylib.py"] = render_tests(methods)
    elif kind == "broken_test_syntax":
        method, asserts = rng.choice(methods)
        asserts[0] = asserts[0][:-1]  # без закрывающей скобки
        files["test_mylib.py"] = render_tests(methods)
    return task
