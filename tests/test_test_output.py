"""Сжатие вывода unittest для промпта. Ожидаемое — из формата вывода unittest (Python 3.12): из каждого провала
остаются имя теста, кадры (файл без пути, строка, функция, исходная строка) и первая строка исключения."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.test_output import compact_unittest  # noqa: E402

RAW = """...FF.E
======================================================================
ERROR: test_div (__main__.T.test_div)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/data/tasks/coding_0001/workspace/test_mylib.py", line 9, in test_div
    self.assertEqual(m.div(4, 2), 2)
                     ~~~~~^^^^^^
  File "/data/tasks/coding_0001/workspace/mylib.py", line 3, in div
    return a / b0
               ^^
NameError: name 'b0' is not defined

======================================================================
FAIL: test_dedupe (__main__.T.test_dedupe)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/data/tasks/coding_0001/workspace/test_mylib.py", line 49, in test_dedupe
    self.assertEqual(m.dedupe([3, 1, 3, 2]), [3, 1, 2])
    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: Lists differ: [3, 1, 3, 2] != [3, 1, 2]

First differing element 2:
3
2

- [3, 1, 3, 2]
?        ---

+ [3, 1, 2]

======================================================================
FAIL: test_factorial (__main__.T.test_factorial)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/data/tasks/coding_0001/workspace/test_mylib.py", line 53, in test_factorial
    self.assertEqual(m.factorial(5), 120)
    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
AssertionError: 24 != 120

----------------------------------------------------------------------
Ran 7 tests in 0.001s

FAILED (failures=2, errors=1)
"""


TRICKY = """EEFFF
hello stdout
======================================================================
ERROR: test_chain (__main__.T.test_chain)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/ws/test_mylib.py", line 5, in test_chain
    1/0
    ~^~
ZeroDivisionError: division by zero

During handling of the above exception, another exception occurred:

Traceback (most recent call last):
  File "/ws/test_mylib.py", line 7, in test_chain
    raise ValueError("second")
ValueError: second

======================================================================
ERROR: test_imp (__main__.T.test_imp)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/ws/test_mylib.py", line 15, in test_imp
    import mylib
  File "/ws/mylib.py", line 1
    def f(:
          ^
SyntaxError: invalid syntax

======================================================================
FAIL: test_ml (__main__.T.test_ml)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/ws/test_mylib.py", line 13, in test_ml
    self.assertEqual(m.text(), "a\\nX\\nc\\n")
AssertionError: 'aaaaaaaaaaaaaaaaaaaa[40 chars]\\nb\\n' != 'aaaaaaaaaaaaaaaaaaaa[40 chars]\\nX\\n'
  aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
- b
+ X


======================================================================
FAIL: test_msg (__main__.T.test_msg)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/ws/test_mylib.py", line 18, in test_msg
    self.assertTrue(m.ok(), "first\\nsecond detail")
AssertionError: False is not true : first
second detail

======================================================================
FAIL: test_sub (__main__.T.test_sub) (i=1)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/ws/test_mylib.py", line 11, in test_sub
    self.assertEqual(m.parity(i), 0)
AssertionError: 1 != 0

----------------------------------------------------------------------
Ran 5 tests in 0.007s

FAILED (failures=3, errors=2)
"""


class ReviewShapes(unittest.TestCase):
    def setUp(self):
        self.out = compact_unittest(TRICKY)

    def test_chained_exception_keeps_final_error(self):
        self.assertIn("ValueError: second", self.out)
        self.assertIn("During handling of the above exception", self.out)

    def test_syntax_error_keeps_file_line_and_source(self):
        self.assertIn("mylib.py:1: def f(:", self.out)
        self.assertIn("SyntaxError: invalid syntax", self.out)

    def test_shortened_diff_keeps_details(self):
        self.assertIn("- b", self.out)
        self.assertIn("+ X", self.out)

    def test_multiline_message_kept(self):
        self.assertIn("second detail", self.out)

    def test_subtest_params_kept(self):
        self.assertIn("FAIL: test_sub (i=1)", self.out)

    def test_printed_output_kept(self):
        self.assertIn("hello stdout", self.out)

    def test_script_error_line_not_unittest(self):
        raw = ("ERROR: config missing, using defaults\nTraceback (most recent call last):\n"
               '  File "/ws/run.py", line 3, in <module>\n    cfg["k"]\nKeyError: \'k\'\n')
        self.assertEqual(compact_unittest(raw), raw)


class Compact(unittest.TestCase):
    def test_keeps_essentials_drops_noise(self):
        out = compact_unittest(RAW)
        for kept in ("ERROR: test_div", "test_mylib.py:9 in test_div: self.assertEqual(m.div(4, 2), 2)",
                     "mylib.py:3 in div: return a / b0", "NameError: name 'b0' is not defined",
                     "FAIL: test_dedupe", "AssertionError: Lists differ: [3, 1, 3, 2] != [3, 1, 2]",
                     "FAIL: test_factorial", "AssertionError: 24 != 120", "FAILED (failures=2, errors=1)"):
            self.assertIn(kept, out)
        for dropped in ("=====", "Traceback (most recent", "/data/tasks", "~~~", "^^", "First differing", "?        ---"):
            self.assertNotIn(dropped, out)
        self.assertLess(len(out), len(RAW) / 2)

    def test_not_unittest_output_unchanged(self):
        raw = "Traceback (most recent call last):\n  File \"x.py\", line 1\nSyntaxError: bad\n"
        self.assertEqual(compact_unittest(raw), raw)

    def test_all_passed_unchanged(self):
        raw = "....\n----------------------------------------------------------------------\nRan 4 tests in 0.001s\n\nOK\n"
        self.assertEqual(compact_unittest(raw), raw)


if __name__ == "__main__":
    unittest.main()
