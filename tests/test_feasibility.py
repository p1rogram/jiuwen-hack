"""Детектор невыполнимых. Ожидания — из spec (specs/task-gen-feasibility/spec.md), примеров организаторов
(все 8 выполнимы, search_0005 → Osaka) и разметки генератора (_labels.json)."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.feasibility import FeasibilityDetector, blocking  # noqa: E402
from harness.feasibility.search import SearchCheck  # noqa: E402
from harness.settings import load_config  # noqa: E402
from harness.tasks import Task, load_tasks  # noqa: E402
from tools.gen.__main__ import generate  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "data" / "task-examples" / "task-dir"
CFG = load_config("feasibility.json")
TYPES = ["coding", "data_analysis", "math", "search"]
CODING_INSTRUCTION = json.loads((EXAMPLES / "coding_0003" / "task.json").read_text(encoding="utf-8"))["instruction"]
DATA_INSTRUCTION = ("The file sales.csv has columns order_id,region,product,month,units,price (3 rows). "
                    "How many units of product 'widget' were sold in months 7 through 8 inclusive? "
                    "Use bash with python3 to compute it.")


def codes(signals, severity="hard"):
    return sorted(s.code for s in signals if s.severity == severity)


class Base(unittest.TestCase):
    def setUp(self):
        self.detector = FeasibilityDetector(CFG)
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_task(self, ttype: str, instruction: str, files: dict[str, str]) -> Task:
        folder = self.tmp / ttype
        ws = folder / "workspace"
        ws.mkdir(parents=True)
        for name, text in files.items():
            (ws / name).parent.mkdir(parents=True, exist_ok=True)
            (ws / name).write_text(text, encoding="utf-8")
        return Task(ttype, ttype, instruction, 1, "x", folder)


class OrganizerExamples(Base):
    def test_no_signals_on_examples(self):
        for task in load_tasks(EXAMPLES):
            self.assertEqual(self.detector.check(task), [], task.task_id)

    def test_search_0005_resolves_to_expected(self):
        task = next(t for t in load_tasks(EXAMPLES) if t.task_id == "search_0005")
        self.assertEqual(SearchCheck(CFG["search"]).resolve(task).answer, "Osaka")

    def test_unknown_person_in_search_0005_is_hard(self):
        src = EXAMPLES / "search_0005"
        shutil.copytree(src, self.tmp / "s")
        task = load_tasks(self.tmp)[0]
        task = Task(task.task_id, task.task_type, task.instruction.replace("Sergei Garcia", "Sergei Lopez"),
                    1, "exact_str", task.task_dir)
        self.assertEqual(codes(self.detector.check(task)), ["unknown_entity"])


class CodingPairs(Base):
    LIB = "def total(xs):\n    return sum(xs)\n"

    def render_tests(self, *asserts):
        body = "\n".join(f"        {a}" for a in asserts)
        return f"import unittest\n\nimport mylib as m\n\n\nclass T(unittest.TestCase):\n    def test_a(self):\n{body}\n"

    def test_same_function_different_calls_is_feasible(self):
        tests = self.render_tests("self.assertEqual(m.total([1, 2, 3]), 6)", "self.assertEqual(m.total([1, 2]), 3)")
        task = self.make_task("coding", CODING_INSTRUCTION, {"mylib.py": self.LIB, "test_mylib.py": tests})
        self.assertEqual(self.detector.check(task), [])

    def test_same_call_two_answers_is_hard(self):
        tests = self.render_tests("self.assertEqual(m.total([1, 2, 3]), 6)", "self.assertEqual(m.total([1, 2, 3]), 7)")
        task = self.make_task("coding", CODING_INSTRUCTION, {"mylib.py": self.LIB, "test_mylib.py": tests})
        self.assertEqual(codes(self.detector.check(task)), ["contradictory_tests"])

    def test_syntax_error_in_tests_is_hard(self):
        tests = self.render_tests("self.assertEqual(m.total([1, 2, 3]), 6")
        task = self.make_task("coding", CODING_INSTRUCTION, {"mylib.py": self.LIB, "test_mylib.py": tests})
        self.assertEqual(codes(self.detector.check(task)), ["test_syntax_error"])

    def test_missing_lib_is_only_soft(self):
        tests = self.render_tests("self.assertEqual(m.total([1, 2, 3]), 6)")
        task = self.make_task("coding", CODING_INSTRUCTION, {"test_mylib.py": tests})
        signals = self.detector.check(task)
        self.assertEqual(codes(signals), [])
        self.assertEqual(codes(signals, "soft"), ["missing_file"])


class DataPairs(Base):
    HEADER = "order_id,region,product,month,units,price\n"

    def test_product_present_but_no_sales_in_months_is_feasible(self):
        csv_text = self.HEADER + "1,west,widget,3,5,10\n2,east,gadget,7,4,20\n3,west,widget,11,2,30\n"
        task = self.make_task("data_analysis", DATA_INSTRUCTION, {"sales.csv": csv_text})
        self.assertEqual(self.detector.check(task), [])

    def test_declared_units_column_missing_is_hard(self):
        csv_text = "order_id,region,product,month,price\n1,west,widget,7,10\n"
        task = self.make_task("data_analysis", DATA_INSTRUCTION, {"sales.csv": csv_text})
        self.assertEqual(codes(self.detector.check(task)), ["missing_column"])

    EMPLOYEES = "id,name,department,salary,years\n1,Ann,sales,3000,4\n2,Bob,support,2800,2\n"
    BONUS_DECLARED = "The file employees.csv has columns id,name,department,salary,years,bonus. "

    def test_declared_column_asked_but_missing_is_hard(self):
        # probe1 data_analysis_p06: 450 токенов на задачу без данных для ответа
        text = self.BONUS_DECLARED + "What is the total bonus paid to the 'sales' department?"
        task = self.make_task("data_analysis", text, {"employees.csv": self.EMPLOYEES})
        self.assertEqual(codes(self.detector.check(task)), ["missing_column"])

    def test_declared_column_missing_but_not_asked_is_not_hard(self):
        text = self.BONUS_DECLARED + "What is the total salary of the 'sales' department?"
        task = self.make_task("data_analysis", text, {"employees.csv": self.EMPLOYEES})
        self.assertEqual(codes(self.detector.check(task)), [])

    def test_declared_column_asked_in_headerless_csv_is_not_hard(self):
        body = "1,Ann,sales,3000,4,100\n2,Bob,support,2800,2,50\n"
        text = self.BONUS_DECLARED + "What is the total bonus paid to the 'sales' department?"
        task = self.make_task("data_analysis", text, {"employees.csv": body})
        self.assertEqual(codes(self.detector.check(task)), [])

    def test_missing_csv_is_hard(self):
        task = self.make_task("data_analysis", DATA_INSTRUCTION, {})
        self.assertEqual(codes(self.detector.check(task)), ["missing_file"])

    def test_unknown_product_is_soft(self):
        csv_text = self.HEADER + "1,west,gadget,7,5,10\n"
        task = self.make_task("data_analysis", DATA_INSTRUCTION, {"sales.csv": csv_text})
        signals = self.detector.check(task)
        self.assertEqual(codes(signals), [])
        self.assertEqual(codes(signals, "soft"), ["unknown_value"])


class Robustness(Base):
    def test_checker_crash_becomes_info_signal(self):
        class Boom:
            def check(self, task):
                raise RuntimeError("boom")

        self.detector._by_type["math"] = Boom()
        task = self.make_task("math", "A warehouse starts with 5 boxes. How many boxes does it have?", {})
        signals = self.detector.check(task)
        self.assertEqual([(s.code, s.severity) for s in signals], [("detector_error", "info")])
        self.assertEqual(blocking(signals, ["hard"]), [])

    def test_blocking_respects_policy(self):
        tests = "import unittest\nimport mylib as m\n"
        task = self.make_task("coding", CODING_INSTRUCTION, {"test_mylib.py": tests})
        signals = self.detector.check(task)
        self.assertEqual(blocking(signals, ["hard"]), [])
        self.assertEqual(len(blocking(signals, ["hard", "soft"])), 1)


MATH_TAIL = " How many boxes does it have at the end? Compute it with the bash tool (python3 -c)."


class Paraphrases(Base):
    """Выполнимые задачи примеров, пересказанные свободнее шаблона: ни одного hard — ложный пропуск стоит баллов
    задачи. Правило vague_quantity: hard — только «some/several/a few» на месте числа операции (как в генераторе:
    «ships some boxes», «for a few days»), иначе soft. Источник — описание бага в docs/DECISIONS.md, 2026-11-10."""

    FEASIBLE_MATH = [
        "A warehouse starts with 1669 boxes. Some boxes are shipped: 36 boxes per day for 3 days. "
        "It receives 2 deliveries of 87 boxes each.",
        "A warehouse with several loading docks starts with 1669 boxes. It ships 36 boxes per day for 3 days.",
        "A warehouse starts with 1669 boxes. It ships 36 boxes per day for 3 days, and a few days later it "
        "receives 2 deliveries of 87 boxes each.",
        "A warehouse, one of several in the region, starts with 1669 boxes. It sells 40 boxes.",
    ]
    VAGUE_MATH = [
        "A warehouse starts with 1669 boxes. It ships some boxes per day for 3 days.",
        "A warehouse starts with 1669 boxes. It ships 36 boxes per day for a few days.",
        "A warehouse starts with 1669 boxes. It receives several deliveries of 87 boxes each.",
        "A warehouse starts with 1669 boxes. It receives 2 deliveries of some boxes each.",
        "A warehouse starts with 1669 boxes. It sells a few boxes.",
        "A warehouse starts with a certain number of boxes. It sells 40 boxes.",
    ]

    def test_free_wording_with_vague_words_is_not_hard(self):
        for i, text in enumerate(self.FEASIBLE_MATH):
            with self.subTest(text=text):
                self.assertEqual(codes(self.detector.check(self.math_task(i, text))), [])

    def test_vague_word_in_place_of_a_number_is_hard(self):
        for i, text in enumerate(self.VAGUE_MATH):
            with self.subTest(text=text):
                self.assertEqual(codes(self.detector.check(self.math_task(i, text))), ["vague_quantity"])

    def test_free_vague_word_is_still_soft(self):
        self.assertEqual(codes(self.detector.check(self.math_task(0, self.FEASIBLE_MATH[0])), "soft"), ["vague_quantity"])

    def math_task(self, i: int, text: str) -> Task:
        folder = self.tmp / f"math{i}"
        (folder / "workspace").mkdir(parents=True, exist_ok=True)
        return Task(f"math{i}", "math", text + MATH_TAIL, 1, "exact_int", folder)


KB = {
    "kb/company_arktika_labs.md": "# Arktika Labs\n\nHeadquarters: Osaka\nFounded: 1990\n",
    "kb/company_bluefin_systems.md": "# Bluefin Systems\n\nHeadquarters: Lima\nFounded: 2001\n",
    "kb/person_irina_smith.md": "# Irina Smith\n\nBorn: 1975\nEmployer: Arktika Labs\nMentor: Sergei Garcia\n",
    "kb/person_sergei_garcia.md": "# Sergei Garcia\n\nBorn: 1960\nEmployer: Bluefin Systems\nMentor: Irina Smith\n",
}
SEARCH_INTRO = "The directory kb/ contains a small knowledge base of markdown files. "
SEARCH_TAIL = " Use the file tools or bash (ls, grep, cat) to find it."
DATA_Q = ("How many units of product 'widget' were sold in months 7 through 8 inclusive? "
          "Use bash with python3 to compute it.")
SALES = "order_id,region,product,month,units,price\n1,west,widget,7,5,10\n2,east,gadget,8,4,20\n"


class HardRuleParaphrases(Base):
    """Выполнимые задачи, пересказанные или оформленные иначе, чем в генераторе: ни одного hard (hard — пропуск
    задачи, ложный стоит её баллов). Ожидание «выполнима» — по построению: ответ есть в файлах задачи."""

    def task_at(self, name: str, ttype: str, instruction: str, files: dict[str, str | bytes]) -> Task:
        folder = self.tmp / name
        ws = folder / "workspace"
        ws.mkdir(parents=True)
        for rel, body in files.items():
            (ws / rel).parent.mkdir(parents=True, exist_ok=True)
            if isinstance(body, bytes):
                (ws / rel).write_bytes(body)
            else:
                (ws / rel).write_text(body, encoding="utf-8")
        return Task(name, ttype, instruction, 1, "x", folder)

    def assert_no_hard(self, task: Task):
        self.assertEqual(codes(self.detector.check(task)), [], task.instruction)

    def test_files_nested_or_written_with_other_verbs(self):
        cases = [
            ("nested_csv", "data_analysis", "The directory data/ contains sales.csv with columns "
             "order_id,region,product,month,units,price. " + DATA_Q, {"data/sales.csv": SALES}),
            ("put_verb", "data_analysis", "The file sales.csv has columns order_id,region,product,month,units,price. "
             + DATA_Q + " Put ONLY the final answer into the file result.txt.", {"sales.csv": SALES}),
            ("nested_dir", "search", "The folder project/ holds a knowledge base in kb/. "
             "In which year was Irina Smith born?" + SEARCH_TAIL, {f"project/{k}": v for k, v in KB.items()}),
        ]
        for name, ttype, text, files in cases:
            with self.subTest(name):
                self.assert_no_hard(self.task_at(name, ttype, text, files))

    def test_data_csv_dialects(self):
        semicolon = SALES.replace(",", ";")
        bom_month_first = "﻿month,units,product,region,order_id,price\n7,5,widget,west,1,10\n"
        cases = [
            ("semicolon", "The file sales.csv has columns order_id,region,product,month,units,price. " + DATA_Q,
             semicolon),
            ("bom", "The file sales.csv has columns month,units,product,region,order_id,price. " + DATA_Q,
             bom_month_first),
        ]
        for name, text, body in cases:
            with self.subTest(name):
                self.assert_no_hard(self.task_at(name, "data_analysis", text, {"sales.csv": body.encode("utf-8")}))

    def test_search_free_wording_and_kb_formats(self):
        linked = dict(KB)
        linked["kb/person_irina_smith.md"] = KB["kb/person_irina_smith.md"].replace(
            "Employer: Arktika Labs", "Employer: [Arktika Labs](company_arktika_labs.md)")
        wiki = dict(KB)
        wiki["kb/person_irina_smith.md"] = KB["kb/person_irina_smith.md"].replace(
            "Employer: Arktika Labs", "Employer: [[Arktika Labs]]")
        suffixed = dict(KB)
        suffixed["kb/person_irina_smith.md"] = KB["kb/person_irina_smith.md"].replace(
            "Employer: Arktika Labs", "Employer: Arktika Labs Inc.")
        hq = "In which city is the headquarters of Irina Smith's employer?"
        cases = [
            ("capitalized_phrase", "Which company in the Knowledge Base has its headquarters in Osaka?", KB),
            ("unknown_relation_word", "In which year was Irina Smith's company established?", KB),
            ("md_link", hq, linked),
            ("wikilink", hq, wiki),
            ("name_suffix", hq, suffixed),
        ]
        for name, question, kb in cases:
            with self.subTest(name):
                self.assert_no_hard(self.task_at(name, "search", SEARCH_INTRO + question + SEARCH_TAIL, kb))

    def test_coding_state_between_calls_is_not_contradiction(self):
        lib = ("class Counter:\n    def __init__(self):\n        self.n = 0\n\n    def next(self):\n"
               "        self.n += 1\n        return self.n\n\n\ndef push(xs, x):\n    xs.append(x)\n\n\n"
               "def size(xs):\n    return len(xs)\n")
        tests = ("import unittest\n\nimport mylib as m\n\n\nclass T(unittest.TestCase):\n"
                 "    def test_counter(self):\n        c = m.Counter()\n"
                 "        self.assertEqual(c.next(), 1)\n        self.assertEqual(c.next(), 2)\n\n"
                 "    def test_stack(self):\n        xs = []\n        m.push(xs, 1)\n"
                 "        self.assertEqual(m.size(xs), 1)\n        m.push(xs, 2)\n"
                 "        self.assertEqual(m.size(xs), 2)\n")
        task = self.task_at("stateful", "coding", CODING_INSTRUCTION, {"mylib.py": lib, "test_mylib.py": tests})
        self.assert_no_hard(task)

    def test_math_zero_that_is_not_a_divisor(self):
        cases = [
            "A warehouse starts with 1669 boxes. Its loading rate, divided by 0.5, is not needed here. It sells 40 boxes.",
            "A warehouse starts with 1669 boxes. Each pallet holds between 0 and 12 boxes. It sells 40 boxes.",
            "A warehouse starts with 1669 boxes. A truck splits its load into 0.5-ton crates. It sells 40 boxes.",
        ]
        for i, text in enumerate(cases):
            with self.subTest(text):
                self.assert_no_hard(self.task_at(f"m{i}", "math", text + MATH_TAIL, {}))


class AuditFalseHards(Base):
    """Аудит edc1c14, A2–A6: входы ревью, на которых детектор ставил hard (пропуск), хотя задача выполнима по
    построению — ответ есть в файлах. Рядом — близкие случаи, которые обязаны остаться hard (генератор)."""

    task_at = HardRuleParaphrases.task_at
    assert_no_hard = HardRuleParaphrases.assert_no_hard

    ANSWER_TAIL = (" Work only inside the current directory. When you are done, write ONLY the final numeric answer to "
                   "the file final_result.txt in the current directory.")

    def test_file_mentioned_not_as_input_is_not_hard(self):
        base = "The file sales.csv has columns order_id,region,product,month,units,price. " + DATA_Q
        cases = [
            ("go_to", base + " The results should go to results.csv as well."),
            ("name_file", base + " Then name that file result.txt."),
            ("not_provided", base + " Do not use pandas (no requirements.txt is provided)."),
            ("long_answer_tail", base + self.ANSWER_TAIL),
        ]
        for name, text in cases:
            with self.subTest(name):
                self.assert_no_hard(self.task_at(name, "data_analysis", text, {"sales.csv": SALES}))

    def test_slash_word_is_not_a_directory(self):
        text = "A warehouse starts with 100 boxes. It sells 40 boxes." + MATH_TAIL + " Compute it w/ python3."
        self.assert_no_hard(self.task_at("w_slash", "math", text, {}))

    def test_input_file_or_kb_dir_absent_stays_hard(self):
        # должно быть отклонено как невыполнимое: файл и папка названы входом и их нет (виды генератора)
        data = self.task_at("no_csv", "data_analysis", DATA_INSTRUCTION, {})
        self.assertEqual(codes(self.detector.check(data)), ["missing_file"])
        search = self.task_at("no_kb", "search", SEARCH_INTRO + "In which year was Irina Smith born?" + SEARCH_TAIL, {})
        self.assertEqual(codes(self.detector.check(search)), ["missing_dir"])

    def coding(self, name: str, body: str, lib: str = "def evens(n):\n    return list(range(0, n, 2))\n") -> Task:
        tests = ("import unittest\nfrom unittest import mock\n\nimport mylib\n\n\nclass T(unittest.TestCase):\n"
                 + body)
        return self.task_at(name, "coding", CODING_INSTRUCTION, {"mylib.py": lib, "test_mylib.py": tests})

    def test_compatible_or_conditional_expectations_are_not_hard(self):
        push_lib = "_items = []\n\n\ndef push(x):\n    _items.append(x)\n    return len(_items)\n"
        cases = [
            ("truthy", "    def test_a(self):\n        self.assertTrue(mylib.evens(4))\n"
                       "        self.assertEqual(mylib.evens(4), [0, 2])\n", None),
            ("stateful", "    def test_a(self):\n        self.assertEqual(mylib.push(1), 1)\n"
                         "        self.assertEqual(mylib.push(1), 2)\n", push_lib),
            ("expected_failure", "    def test_a(self):\n        self.assertEqual(mylib.evens(4), [0, 2])\n\n"
                                 "    @unittest.expectedFailure\n    def test_b(self):\n"
                                 "        self.assertEqual(mylib.evens(4), [0])\n", None),
            ("patched", "    def test_a(self):\n        self.assertEqual(mylib.evens(4), [0, 2])\n\n"
                        "    def test_b(self):\n        with mock.patch.object(mylib, 'STEP', 4):\n"
                        "            self.assertEqual(mylib.evens(4), [0])\n", None),
        ]
        for name, body, lib in cases:
            with self.subTest(name):
                self.assert_no_hard(self.coding(name, body, *([lib] if lib else [])))

    def test_pure_call_two_answers_in_different_methods_stays_hard(self):
        # должно быть отклонено: вид генератора — один вызов чистой функции, два разных значения
        body = ("    def test_a(self):\n        self.assertEqual(mylib.evens(4), [0, 2])\n\n"
                "    def test_b(self):\n        self.assertEqual(mylib.evens(4), [0, 2, 4])\n")
        self.assertEqual(codes(self.detector.check(self.coding("clash", body))), ["contradictory_tests"])

    def test_reverse_relation_question_is_not_hard(self):
        kb = dict(KB)
        kb["kb/person_chen_wang.md"] = "# Chen Wang\n\nBorn: 1950\nEmployer: Bluefin Systems\n"
        kb["kb/person_irina_smith.md"] = KB["kb/person_irina_smith.md"].replace("Sergei Garcia", "Chen Wang")
        text = SEARCH_INTRO + "Which person has Chen Wang as a mentor?" + SEARCH_TAIL
        self.assert_no_hard(self.task_at("reverse", "search", text, kb))

    def test_direct_missing_field_stays_hard(self):
        # должно быть отклонено: у Irina Smith нет Born, у других людей есть — вид генератора missing_field
        kb = dict(KB)
        kb["kb/person_irina_smith.md"] = KB["kb/person_irina_smith.md"].replace("Born: 1975\n", "")
        text = SEARCH_INTRO + "In which year was Irina Smith born?" + SEARCH_TAIL
        self.assertEqual(codes(self.detector.check(self.task_at("no_born", "search", text, kb))), ["missing_field"])

    def test_csv_without_header_is_not_hard(self):
        body = "1,west,widget,7,5,10\n2,east,gadget,8,4,20\n"
        text = "The file sales.csv has columns order_id,region,product,month,units,price. " + DATA_Q
        self.assert_no_hard(self.task_at("headerless", "data_analysis", text, {"sales.csv": body}))


class GeneratedSets(Base):
    def test_no_hard_on_400_feasible_tasks(self):
        generate(self.tmp / "ok", 400, 21, 0.0, 0.5, TYPES)
        flagged = {t.task_id: codes(self.detector.check(t)) for t in load_tasks(self.tmp / "ok")}
        self.assertEqual({k: v for k, v in flagged.items() if v}, {})

    def test_hard_kinds_recall_at_least_90_percent(self):
        labels = generate(self.tmp / "bad", 160, 22, 1.0, 0.5, TYPES)
        hard = [t for t in load_tasks(self.tmp / "bad") if labels[t.task_id]["severity"] == "hard"]
        caught = sum(1 for t in hard if blocking(self.detector.check(t), ["hard"]))
        self.assertGreater(len(hard), 80)
        self.assertGreaterEqual(caught / len(hard), 0.9)


if __name__ == "__main__":
    unittest.main()
