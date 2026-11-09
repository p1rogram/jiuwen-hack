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
