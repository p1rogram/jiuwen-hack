"""Вклейка: случаи из ревью f0ed9ac (2026-11-10). Ожидаемое — из specs/coding-ab/spec.md пункт г) (класс по членам,
одиночный метод — в свой класс) и семантики Python: правка не должна молча терять или портить код."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.patching import PatchError, merge_source  # noqa: E402


def run(text: str) -> dict:
    ns: dict = {"__name__": "mylib"}
    exec(compile(text, "mylib.py", "exec"), ns)
    return ns


class MethodVsModuleFunction(unittest.TestCase):
    ORIG = (
        "def validate(x):\n    return x > 0\n\n\n"
        "class A:\n    def validate(self):\n        return 'old'\n"
    )

    def test_lone_method_goes_to_class_not_to_module_function(self):
        text = merge_source(self.ORIG, "def validate(self):\n    return 'new'\n")
        ns = run(text)
        self.assertTrue(ns["validate"](1))
        self.assertEqual(ns["A"]().validate(), "new")

    def test_plain_function_still_replaces_module_function(self):
        ns = run(merge_source(self.ORIG, "def validate(x):\n    return x >= 0\n"))
        self.assertTrue(ns["validate"](0))
        self.assertEqual(ns["A"]().validate(), "old")


class SameNameDefinitions(unittest.TestCase):
    ORIG = (
        "class Box:\n"
        "    def __init__(self):\n        self._v = 1\n\n"
        "    @property\n    def v(self):\n        return self._v\n\n"
        "    @v.setter\n    def v(self, value):\n        self._v = value\n"
    )

    def test_getter_fix_keeps_setter(self):
        text = merge_source(self.ORIG, "@property\ndef v(self):\n    return self._v * 10\n")
        b = run(text)["Box"]()
        b.v = 2
        self.assertEqual(b.v, 20)

    def test_getter_fix_in_class_keeps_setter(self):
        text = merge_source(self.ORIG, "class Box:\n    @property\n    def v(self):\n        return self._v * 10\n")
        b = run(text)["Box"]()
        b.v = 3
        self.assertEqual(b.v, 30)

    def test_setter_fix_keeps_getter(self):
        text = merge_source(self.ORIG, "@v.setter\ndef v(self, value):\n    self._v = value + 1\n")
        b = run(text)["Box"]()
        b.v = 3
        self.assertEqual(b.v, 4)

    def test_undecorated_def_over_getter_and_setter_rejected(self):
        with self.assertRaises(PatchError):
            merge_source(self.ORIG, "class Box:\n    def v(self):\n        return 0\n")

    def test_overload_implementation_replaced(self):
        orig = (
            "from typing import overload\n\n\n"
            "@overload\ndef f(x: int) -> int: ...\n\n\n"
            "@overload\ndef f(x: str) -> str: ...\n\n\n"
            "def f(x):\n    return x\n"
        )
        text = merge_source(orig, "def f(x):\n    return x * 2\n")
        self.assertEqual(text.count("@overload"), 2)
        self.assertEqual(run(text)["f"](2), 4)

    def test_redefined_function_last_one_replaced(self):
        orig = "def g():\n    return 1\n\n\ndef g():\n    return 2\n"
        self.assertEqual(run(merge_source(orig, "def g():\n    return 3\n"))["g"](), 3)


class ClassHeader(unittest.TestCase):
    ORIG = (
        "class Base:\n    kind = 'base'\n\n\n"
        "class Other:\n    kind = 'other'\n\n\n"
        "class A(Base):\n    def f(self):\n        return 1\n\n    def g(self):\n        return 2\n"
    )

    def test_changed_base_applied_members_kept(self):
        text = merge_source(self.ORIG, "class A(Other):\n    def f(self):\n        return 10\n")
        a = run(text)["A"]()
        self.assertEqual((a.kind, a.f(), a.g()), ("other", 10, 2))

    def test_added_decorator_applied(self):
        orig = "class P:\n    x: int = 0\n\n    def f(self):\n        return 1\n"
        patch = "from dataclasses import dataclass\n\n\n@dataclass\nclass P:\n    def f(self):\n        return 2\n"
        text = merge_source(orig, patch)
        ns = run(text)
        self.assertEqual(ns["P"](5).x, 5)
        self.assertEqual(ns["P"]().f(), 2)

    def test_bare_partial_class_keeps_base_and_decorator(self):
        orig = ("from dataclasses import dataclass\n\n\nclass Base:\n    kind = 'base'\n\n\n"
                "@dataclass\nclass A(Base):\n    x: int = 0\n\n    def m(self):\n        return 1\n")
        ns = run(merge_source(orig, "class A:\n    def m(self):\n        return 2\n"))
        a = ns["A"](7)
        self.assertEqual((a.x, a.kind, a.m()), (7, "base", 2))

    def test_patch_base_without_old_decorator_keeps_old_header(self):
        orig = ("from dataclasses import dataclass\n\n\nclass Base:\n    kind = 'base'\n\n\n"
                "@dataclass\nclass A(Base):\n    x: int = 0\n\n    def m(self):\n        return 1\n")
        ns = run(merge_source(orig, "class A(Base):\n    def m(self):\n        return 2\n"))
        self.assertEqual((ns["A"](7).x, ns["A"]().m()), (7, 2))

    def test_same_header_untouched(self):
        text = merge_source(self.ORIG, "class A(Base):\n    def f(self):\n        return 10\n")
        self.assertIn("class A(Base):\n", text)
        self.assertEqual(text.count("class A"), 1)


class LineBreaks(unittest.TestCase):
    def test_form_feed_in_original_does_not_shift_ranges(self):
        orig = "S = 'a\x0cb'\n\n\ndef g():\n    return 1\n\n\ndef h():\n    return 2\n"
        text = merge_source(orig, "def g():\n    return 5\n")
        ns = run(text)
        self.assertEqual((ns["S"], ns["g"](), ns["h"]()), ("a\x0cb", 5, 2))
        self.assertEqual(text.count("return"), 2)

    def test_line_separator_in_patch_string(self):
        orig = "def g():\n    return 1\n"
        text = merge_source(orig, "def g():\n    return 'x\u2028y'\n")
        self.assertEqual(run(text)["g"](), "x\u2028y")


class MainBlock(unittest.TestCase):
    def test_new_function_inserted_before_main_block(self):
        orig = "def a():\n    return b()\n\n\nif __name__ == '__main__':\n    RESULT = a()\n"
        text = merge_source(orig, "def b():\n    return 7\n")
        ns: dict = {"__name__": "__main__"}
        exec(compile(text, "mylib.py", "exec"), ns)
        self.assertEqual(ns["RESULT"], 7)
        self.assertTrue(text.rstrip().endswith("RESULT = a()"))


class StringIndent(unittest.TestCase):
    def test_multiline_string_inside_reindented_method_unchanged(self):
        orig = "class T:\n    def text(self):\n        return 0\n"
        patch = 'def text(self):\n    return """line1\nline2\n  line3"""\n'
        text = merge_source(orig, patch)
        self.assertEqual(run(text)["T"]().text(), "line1\nline2\n  line3")


if __name__ == "__main__":
    unittest.main()
