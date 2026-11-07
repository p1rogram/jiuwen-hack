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
