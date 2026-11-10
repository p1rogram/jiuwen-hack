"""Решатель по правилам (specs/rules-solver/spec.md). Ожидания — `expected` примеров организаторов и арифметика
условия; «отклонить» — любое отступление от шаблона даёт None (задачу решит LLM)."""
import asyncio
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.rules import RulesSolver  # noqa: E402
from harness.settings import load_config  # noqa: E402
from harness.tasks import Task, load_tasks  # noqa: E402
from run_agent import Harness  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "data" / "task-examples" / "task-dir"
TAIL = (" Work only inside the current directory. When you are done, write ONLY the final answer to the file "
        "answer.txt in the current directory (for example with bash: echo 42 > answer.txt).")
MATH_Q = " How many boxes does it have at the end? Compute it with the bash tool (python3 -c)." + TAIL
DATA_INTRO = "The file sales.csv has columns order_id,region,product,month,units,price ({n} rows). "
DATA_TOOL = " Use bash with python3 to compute it." + TAIL
SALES = ("order_id,region,product,month,units,price\n1,west,widget,7,5,10\n2,east,widget,8,4,20\n"
         "3,west,gadget,7,9,30\n4,west,widget,9,6,10\n")
SEARCH_INTRO = "The directory kb/ contains a small knowledge base of markdown files. "
SEARCH_TOOL = " Use the file tools or bash (ls, grep, cat) to find it." + TAIL
KB = {
    "kb/company_arktika_labs.md": "# Arktika Labs\n\nHeadquarters: Osaka\nFounded: 1990\n",
    "kb/company_bluefin_systems.md": "# Bluefin Systems\n\nHeadquarters: Lima\nFounded: 2001\n",
    "kb/person_irina_smith.md": "# Irina Smith\n\nBorn: 1975\nEmployer: Arktika Labs\nMentor: Sergei Garcia\n",
    "kb/person_sergei_garcia.md": "# Sergei Garcia\n\nBorn: 1960\nEmployer: Bluefin Systems\nMentor: Irina Smith\n",
}


class Base(unittest.TestCase):
    def setUp(self):
        self.solver = RulesSolver(load_config("rules.json"))
        self.tmp = Path(tempfile.mkdtemp())
        self.n = 0

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def task(self, ttype: str, instruction: str, files: dict[str, str] | None = None) -> Task:
        self.n += 1
        folder = self.tmp / f"t{self.n}"
        (folder / "workspace").mkdir(parents=True)
        for rel, body in (files or {}).items():
            (folder / "workspace" / rel).parent.mkdir(parents=True, exist_ok=True)
            (folder / "workspace" / rel).write_text(body, encoding="utf-8", newline="")
        check = {"math": "exact_int", "data_analysis": "exact_int", "search": "exact_str"}[ttype]
        return Task(f"t{self.n}", ttype, instruction, 1, check, folder)

    def solve(self, ttype: str, text: str, files: dict[str, str] | None = None):
        return self.solver.solve(self.task(ttype, text, files))


class OrganizerExamples(Base):
    def test_all_non_coding_examples_match_expected(self):
        seen = 0
        for task in load_tasks(EXAMPLES):
            if task.task_type == "coding":
                continue
            expected = json.loads((task.task_dir / "task.json").read_text(encoding="utf-8"))["expected"]
            self.assertEqual(self.solver.solve(task), expected, task.task_id)
            seen += 1
        self.assertEqual(seen, 6)


class MathRules(Base):
    def test_template_sentences_are_computed(self):
        text = ("A warehouse starts with 1669 boxes. It ships 36 boxes per day for 3 days. It receives 2 deliveries "
                "of 87 boxes each. It sells 40 boxes. Then the stock is split equally into 3 warehouses and it keeps "
                "one share (rounded down). Then the stock is doubled by merging with a sister warehouse.")
        stock = ((1669 - 36 * 3 + 2 * 87 - 40) // 3) * 2
        self.assertEqual(self.solve("math", text + MATH_Q), str(stock))

    def test_anything_beyond_the_template_is_rejected(self):
        cases = [
            "A warehouse starts with 1669 boxes. It ships 36 boxes per day for 3 days, except on Sundays.",
            "A warehouse starts with 1669 boxes. It ships 36 boxes per day for 3 days. Half of them are returned.",
            "A warehouse starts with 1,669 boxes. It sells 40 boxes.",
            "A warehouse starts with 1669 boxes. Then the stock is split equally into 0 warehouses and it keeps "
            "one share (rounded down).",
            "A warehouse starts with 100 boxes. It ships 60 boxes per day for 2 days.",  # остаток < 0
            "A shop starts with 1669 boxes. It sells 40 boxes.",
        ]
        for text in cases:
            with self.subTest(text):
                self.assertIsNone(self.solve("math", text + MATH_Q))

    def test_other_question_or_two_questions_rejected(self):
        base = "A warehouse starts with 1669 boxes. It sells 40 boxes."
        self.assertIsNone(self.solve("math", base + " How many crates does it have at the end?" + TAIL))
        self.assertIsNone(self.solve("math", base + " How many boxes does it have at the end? And why?" + TAIL))


class DataRules(Base):
    Q = "How many units of product 'widget' were sold in months 7 through 8 inclusive?"

    def test_units_region_and_revenue(self):
        rows = DATA_INTRO.format(n=4)
        region_q = "How many units of product 'widget' were sold in region 'west' in months 7 through 9 inclusive?"
        revenue_q = ("What was the total revenue (sum of units * price) of product 'widget' in months 7 through 8 "
                     "inclusive?")
        self.assertEqual(self.solve("data_analysis", rows + self.Q + DATA_TOOL, {"sales.csv": SALES}), "9")
        self.assertEqual(self.solve("data_analysis", rows + region_q + DATA_TOOL, {"sales.csv": SALES}), "11")
        self.assertEqual(self.solve("data_analysis", rows + revenue_q + DATA_TOOL, {"sales.csv": SALES}), "130")

    def test_file_not_as_described_is_rejected(self):
        cases = [
            (DATA_INTRO.format(n=5), SALES),  # обещано 5 строк, в файле 4
            (DATA_INTRO.format(n=4), SALES.replace("units,price", "qty,price")),
            (DATA_INTRO.format(n=4), SALES.replace("1,west,widget,7,5,10", "1,west,widget,7,5.5,10")),
            (DATA_INTRO.format(n=4), SALES.replace("2,east,widget,8,4,20", "2,east,widget,8,4")),
            (DATA_INTRO.format(n=4), "﻿" + SALES),
        ]
        for intro, body in cases:
            with self.subTest(intro=intro, head=body[:60]):
                self.assertIsNone(self.solve("data_analysis", intro + self.Q + DATA_TOOL, {"sales.csv": body}))

    def test_absent_value_and_months_past_12_give_sum_over_empty_rows(self):
        # specs/token-cuts-2 шаг 2: так же отвечает LLM (run73/81/83 — «0» в 4 из 4), но за 0 токенов
        rows = DATA_INTRO.format(n=4)
        region_q = "How many units of product 'gadget' were sold in region 'east' in months 1 through 12 inclusive?"
        cases = [
            (self.Q.replace("widget", "sprocket"), "0"),
            (self.Q.replace("7 through 8", "13 through 15"), "0"),
            (self.Q.replace("7 through 8", "8 through 13"), "10"),
            (region_q, "0"),  # оба значения есть, вместе — ни в одной строке
            (region_q.replace("'east'", "'north'"), "0"),
        ]
        for q, want in cases:
            with self.subTest(q=q):
                self.assertEqual(self.solve("data_analysis", rows + q + DATA_TOOL, {"sales.csv": SALES}), want)

    def test_value_like_one_in_csv_or_reversed_months_left_to_llm(self):
        rows = DATA_INTRO.format(n=4)
        for q in [self.Q.replace("'widget'", "'Widget'"), self.Q.replace("'widget'", "'widgets'"),
                  self.Q.replace("7 through 8", "8 through 7")]:
            with self.subTest(q=q):
                self.assertIsNone(self.solve("data_analysis", rows + q + DATA_TOOL, {"sales.csv": SALES}))


class SearchRules(Base):
    def test_all_chain_templates(self):
        cases = {
            "In which year was Irina Smith born?": "1975",
            "In which year was Irina Smith's mentor born?": "1960",
            "In which year was Irina Smith's employer founded?": "1990",
            "In which city is the headquarters of Irina Smith's employer?": "Osaka",
            "In which city is the headquarters of the employer of Irina Smith's mentor?": "Lima",
        }
        for question, answer in cases.items():
            with self.subTest(question):
                self.assertEqual(self.solve("search", SEARCH_INTRO + question + SEARCH_TOOL, KB), answer)

    def test_kb_not_in_format_is_rejected(self):
        mentor_q = "In which year was Irina Smith's mentor born?"
        two_mentors = dict(KB)
        two_mentors["kb/person_irina_smith.md"] += "Mentor: Irina Smith\n"
        twin = dict(KB)
        twin["kb/person_irina_smith_2.md"] = "# Irina Smith\n\nBorn: 1980\n"
        broken = dict(KB)
        broken["kb/person_irina_smith.md"] = KB["kb/person_irina_smith.md"].replace("Sergei Garcia", "Sergei Lopez")
        linked = dict(KB)
        linked["kb/person_irina_smith.md"] = KB["kb/person_irina_smith.md"].replace(
            "Mentor: Sergei Garcia", "Mentor: [Sergei Garcia](person_sergei_garcia.md)")
        notes = dict(KB)
        notes["kb/README.txt"] = "people and companies\n"
        prose = dict(KB)
        prose["kb/person_irina_smith.md"] += "She moved to Lima in 2001.\n"
        for name, kb in [("two_mentors", two_mentors), ("twin", twin), ("broken", broken), ("linked", linked),
                         ("notes", notes), ("prose", prose)]:
            with self.subTest(name):
                self.assertIsNone(self.solve("search", SEARCH_INTRO + mentor_q + SEARCH_TOOL, kb))

    def test_other_question_or_unknown_person_rejected(self):
        for q in ["Who is Irina Smith's mentor?", "In which year was Irina Smith's manager born?",
                  "In which year was Irina Lopez born?"]:
            with self.subTest(q):
                self.assertIsNone(self.solve("search", SEARCH_INTRO + q + SEARCH_TOOL, KB))


class FakeWriter:
    def __init__(self):
        self.written = {}

    def record(self, task, answer):
        self.written[task.task_id] = answer
        return ""


class FakeLog:
    def write(self, rec):
        pass


class FakePrompt:
    def __init__(self):
        self.built = []

    async def build(self, task):
        self.built.append(task.task_id)
        return type("Prepared", (), {})()


class Stage0InHarness(Base):
    def harness(self):
        h = Harness.__new__(Harness)
        h.log, h.writer, h.prompt = FakeLog(), FakeWriter(), FakePrompt()
        h.detector, h.skip_severities, h.rules = None, ["hard"], self.solver
        return h

    def test_template_task_answered_without_prompt(self):
        h = self.harness()
        task = self.task("math", "A warehouse starts with 100 boxes. It sells 40 boxes." + MATH_Q)
        rec = asyncio.run(h._prepare(task))
        self.assertEqual((rec["status"], rec["solved_by"], rec["cost"], rec["answer"]), ("verified", "rules", 0, "60"))
        self.assertEqual(h.writer.written, {task.task_id: "60"})
        self.assertEqual(h.prompt.built, [])

    def test_off_template_task_goes_to_llm_path(self):
        h = self.harness()
        task = self.task("math", "A warehouse starts with 100 boxes. It sells 40 boxes on Monday." + MATH_Q)
        asyncio.run(h._prepare(task))
        self.assertEqual(h.prompt.built, [task.task_id])
        self.assertEqual(h.writer.written, {})

    def test_soft_signal_keeps_llm_path(self):
        class SoftDetector:
            def check(self, task):
                from harness.feasibility.model import SOFT, Signal
                return [Signal("unknown_unit", SOFT, "x")]

        h = self.harness()
        h.detector = SoftDetector()
        task = self.task("math", "A warehouse starts with 100 boxes. It sells 40 boxes." + MATH_Q)
        asyncio.run(h._prepare(task))
        self.assertEqual(h.prompt.built, [task.task_id])

    def test_soft_signal_from_soft_ok_answered_by_rules(self):
        class ValueDetector:
            def check(self, task):
                from harness.feasibility.model import SOFT, Signal
                return [Signal("unknown_value", SOFT, "product 'sprocket' not in sales.csv")]

        h = self.harness()
        h.detector = ValueDetector()
        q = DataRules.Q.replace("widget", "sprocket")
        task = self.task("data_analysis", DATA_INTRO.format(n=4) + q + DATA_TOOL, {"sales.csv": SALES})
        rec = asyncio.run(h._prepare(task))
        self.assertEqual((rec["status"], rec["solved_by"], rec["answer"]), ("verified", "rules", "0"))
        self.assertEqual(h.prompt.built, [])


if __name__ == "__main__":
    unittest.main()
