"""Структура ответа этапа 1. Ожидаемые числа — из specs/coding-ab/spec.md, пункт а), по построенным здесь ответам."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.answer_shape import answer_shape  # noqa: E402
from harness.patching import find_patch  # noqa: E402

ORIGINAL = (
    "def total(xs):\n"
    "    s = 0\n"
    "    for x in xs:\n"
    "        s -= x\n"
    "    return s\n"
    "\n\n"
    "def first_n(xs, n):\n"
    "    return xs[:n]\n"
)
SAME_FIRST_N = "def first_n(xs, n):\n    return xs[:n]\n"
FIXED_TOTAL = "def total(xs):\n    return sum(xs)\n"


def shape(reply: str, original: str | None = ORIGINAL) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        if original is not None:
            (ws / "mylib.py").write_bytes(original.encode())
        return answer_shape(reply, find_patch(reply, ws), ws)


class KeyPair(unittest.TestCase):
    def test_identical_function_is_unchanged(self):
        reply = "Fix.\n```python\n# file: mylib.py\n" + FIXED_TOTAL + "\n\n" + SAME_FIRST_N + "```\nDone."
        s = shape(reply)
        self.assertEqual((s["defs"], s["changed"], s["unchanged"], s["new"]), (2, 1, 1, 0))
        self.assertEqual((s["before_chars"], s["after_chars"], s["blocks"]), (5, 6, 1))
        self.assertTrue(s["whole_file"])

    def test_whitespace_and_comment_only_difference_is_unchanged(self):
        cosmetic = "def first_n(xs,   n):\n    # same thing\n    return xs[:n]\n"
        s = shape("```python\n# file: mylib.py\n" + cosmetic + "```")
        self.assertEqual((s["changed"], s["unchanged"]), (0, 1))
        self.assertFalse(s["whole_file"])

    def test_other_body_is_changed(self):
        s = shape("```python\n# file: mylib.py\ndef first_n(xs, n):\n    return list(xs)[:n]\n```")
        self.assertEqual((s["changed"], s["unchanged"]), (1, 0))


class Edges(unittest.TestCase):
    def test_no_code_block(self):
        s = shape("The answer is 42.")
        self.assertEqual(s, {"before_chars": 17, "after_chars": 0, "blocks": 0})

    def test_new_file_functions_are_new(self):
        s = shape("```python\n# file: mylib.py\n" + FIXED_TOTAL + "```", original=None)
        self.assertEqual((s["defs"], s["new"], s["whole_file"]), (1, 1, False))

    def test_broken_original_reports_error(self):
        s = shape("```python\n# file: mylib.py\n" + FIXED_TOTAL + "```", original="def total(:\n")
        self.assertIn("SyntaxError", s["error"])
        self.assertEqual(s["blocks"], 1)

    def test_partial_class_counts_methods(self):
        orig = "class S:\n    def a(self):\n        return 1\n\n    def b(self):\n        return 2\n"
        s = shape("```python\n# file: mylib.py\nclass S:\n    def a(self):\n        return 9\n```", original=orig)
        self.assertEqual((s["defs"], s["changed"], s["unchanged"], s["new"]), (1, 1, 0, 0))

    def test_lone_method_compared_with_class_method(self):
        orig = "class S:\n    def a(self):\n        return 1\n"
        s = shape("```python\n# file: mylib.py\ndef a(self):\n    return 1\n```", original=orig)
        self.assertEqual((s["changed"], s["unchanged"]), (0, 1))


if __name__ == "__main__":
    unittest.main()
