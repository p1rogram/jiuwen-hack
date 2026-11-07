"""Генераторы задач: эталон верен, ответ пересчитывается независимо из текста и файлов, seed воспроизводим.

Ожидаемые значения — из самих сгенерированных файлов (CSV, текст условия, тесты), а не из кода генератора.
"""
import csv
import io
import random
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.gen import coding, data_analysis, math_task, search  # noqa: E402
from tools.gen.common import templates  # noqa: E402
from tools.gen.__main__ import generate  # noqa: E402
from tools.gen.content.coding_pool import POOL  # noqa: E402


def run_tests(files: dict[str, str]) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        for name, text in files.items():
            Path(tmp, name).write_text(text, encoding="utf-8")
        return subprocess.run([sys.executable, "test_mylib.py"], cwd=tmp, capture_output=True).returncode


class CodingPool(unittest.TestCase):
    def test_reference_passes_and_every_bug_breaks_a_case(self):
        for f in POOL:
            ok = coding.compile_func(f["source"], f["name"])
            for args, expected in f["cases"]:
                self.assertEqual(ok(*args), expected, f["name"])
            for bug in f["bugs"]:
                coding.failing_case(f, bug)  # ValueError, если баг не ломает ни одного кейса


class CodingTasks(unittest.TestCase):
    def test_buggy_lib_fails_and_reference_passes(self):
        rng = random.Random(11)
        for variant in ["base", "hard", "base", "hard"]:
            task = coding.make(rng, variant)
            self.assertNotEqual(run_tests(task.files), 0)
            self.assertEqual(run_tests({**task.files, **task.reference}), 0)


def units_answer(task) -> int:
    q = task.instruction
    product = re.search(r"product '([^']+)'", q).group(1)
    region = re.search(r"region '([^']+)'", q)
    m1, m2 = map(int, re.search(r"months (\d+) through (\d+)", q).groups())
    revenue = "revenue" in q
    total = 0
    for row in csv.DictReader(io.StringIO(task.files["sales.csv"])):
        if row["product"] != product or not m1 <= int(row["month"]) <= m2:
            continue
        if region and row["region"] != region.group(1):
            continue
        total += int(row["units"]) * (int(row["price"]) if revenue else 1)
    return total


class DataTasks(unittest.TestCase):
    def test_expected_matches_csv(self):
        rng = random.Random(5)
        for i in range(30):
            task = data_analysis.make(rng, "hard" if i % 2 else "base")
            self.assertEqual(task.expected, str(units_answer(task)))
            rows = int(re.search(r"\((\d+) rows\)", task.instruction).group(1))
            self.assertEqual(len(task.files["sales.csv"].splitlines()) - 1, rows)


def math_answer(text: str) -> int:
    stock = None
    for s in re.split(r"(?<=\.)\s+", text):
        if m := re.search(r"starts with (\d+) boxes", s):
            stock = int(m.group(1))
        elif m := re.search(r"ships (\d+) boxes per day for (\d+) days", s):
            stock -= int(m.group(1)) * int(m.group(2))
        elif m := re.search(r"receives (\d+) deliveries of (\d+) boxes", s):
            stock += int(m.group(1)) * int(m.group(2))
        elif m := re.search(r"sells (\d+) boxes", s):
            stock -= int(m.group(1))
        elif m := re.search(r"split equally into (\d+) warehouses", s):
            stock //= int(m.group(1))
        elif "doubled" in s:
            stock *= 2
        elif s.startswith("How many"):
            break
    return stock


class MathTasks(unittest.TestCase):
    def test_expected_matches_text(self):
        rng = random.Random(9)
        for i in range(40):
            task = math_task.make(rng, "hard" if i % 2 else "base")
            self.assertEqual(task.expected, str(math_answer(task.instruction)))


def kb_cards(files: dict[str, str]) -> dict[str, dict[str, str]]:
    cards = {}
    for text in files.values():
        lines = text.splitlines()
        cards[lines[0][2:]] = dict(ln.split(": ", 1) for ln in lines[2:] if ": " in ln)
    return cards


def walk_chain(task) -> tuple[str | None, str]:
    """Пройти цепочку вопроса по карточкам kb: (ответ, '') или (None, где оборвалась)."""
    tpl = templates()["search"]
    for q in sorted(search.CHAINS, key=lambda k: -len(tpl[k])):  # сначала длинные: «X's mentor born» раньше «X born»
        relations, attr = search.CHAINS[q]
        m = re.search(re.escape(tpl[q]).replace(re.escape("{p}"), "([A-Z][a-z]+ [A-Z][a-z]+)"), task.instruction)
        if m:
            break
    cards = kb_cards(task.files)
    cur = m.group(1)
    for rel in relations:
        if cur not in cards:
            return None, f"no card {cur}"
        cur = cards[cur].get(rel)
    if cur not in cards:
        return None, f"no card {cur}"
    return cards[cur].get(attr), "" if attr in cards[cur] else f"no {attr}"


class SearchTasks(unittest.TestCase):
    def test_feasible_chain_gives_expected(self):
        rng = random.Random(4)
        for i in range(30):
            task = search.make(rng, "hard" if i % 2 else "base")
            self.assertEqual(walk_chain(task), (task.expected, ""))

    def test_broken_chain_really_breaks_the_asked_chain(self):
        # gen2/search_0332: «employer of X's mentor», а сломан был работодатель самого X — задача выполнима
        rng = random.Random(1)
        for _ in range(40):
            task = search.make(rng, "infeasible", "broken_chain")
            answer, where = walk_chain(task)
            self.assertIsNone(answer, task.instruction)
            self.assertTrue(where.startswith("no card"), where)


class SetGeneration(unittest.TestCase):
    def test_same_seed_same_bytes_and_share_of_infeasible(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp, "a"), Path(tmp, "b")
            labels = generate(a, 40, 3, 0.2, 0.5, ["coding", "data_analysis", "math", "search"])
            generate(b, 40, 3, 0.2, 0.5, ["coding", "data_analysis", "math", "search"])
            files_a = sorted(p.relative_to(a) for p in a.rglob("*") if p.is_file())
            files_b = sorted(p.relative_to(b) for p in b.rglob("*") if p.is_file())
            self.assertEqual(files_a, files_b)
            for rel in files_a:
                self.assertEqual((a / rel).read_bytes(), (b / rel).read_bytes(), rel)
            self.assertEqual(sum(not x["feasible"] for x in labels.values()), 8)

    def test_refuses_non_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "x").write_text("1")
            with self.assertRaises(SystemExit):
                generate(Path(tmp), 4, 1, 0.0, 0.5, ["math"])


if __name__ == "__main__":
    unittest.main()
