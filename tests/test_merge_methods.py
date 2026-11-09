"""Вклейка методов класса. Ожидаемое поведение — из specs/coding-ab/spec.md, пункт г), не из кода."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.patching import PatchApplier, PatchError, find_patch, merge_source  # noqa: E402

STACK = (
    "# Стек — комментарий кириллицей выше класса\n"
    "class Stack:\n"
    "    \"\"\"Простой стек.\"\"\"\n"
    "    limit = 10\n"
    "\n"
    "    def __init__(self):\n"
    "        self.items = []\n"
    "\n"
    "    def push(self, x):\n"
    "        self.items.insert(0, x)\n"
    "\n"
    "    def pop(self):\n"
    "        return self.items.pop()\n"
    "\n"
    "\n"
    "def helper():\n"
    "    return 1\n"
)
PUSH_FIXED = "def push(self, x):\n    self.items.append(x)\n"
TWO_CLASSES = (
    "class A:\n    def push(self, x):\n        return 1\n\n\n"
    "class B:\n    def push(self, x):\n        return 2\n"
)


class KeyPair(unittest.TestCase):
    def test_lone_method_goes_into_its_single_class(self):
        text = merge_source(STACK, PUSH_FIXED)
        self.assertIn("    def push(self, x):\n        self.items.append(x)\n", text)
        self.assertNotIn("insert(0, x)", text)
        self.assertIn("    def pop(self):\n        return self.items.pop()\n", text)
        self.assertIn("def helper():\n    return 1\n", text)
        self.assertEqual(text.count("def push"), 1)

    def test_method_in_two_classes_rejected_file_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            (ws / "mylib.py").write_bytes(TWO_CLASSES.encode())
            run = PatchApplier().apply(find_patch("```python\n# file: mylib.py\n" + PUSH_FIXED + "```", ws), ws)
            self.assertEqual((ws / "mylib.py").read_bytes().decode(), TWO_CLASSES)
        self.assertFalse(run.ok)
        self.assertIn("several classes", run.stderr)
        self.assertIn("A, B", run.stderr)


class ClassMembers(unittest.TestCase):
    def test_partial_class_keeps_other_members(self):
        patch = "class Stack:\n    def push(self, x):\n        self.items.append(x)\n"
        text = merge_source(STACK, patch)
        self.assertIn("self.items.append(x)", text)
        self.assertNotIn("insert(0, x)", text)
        for kept in ("limit = 10", "def __init__(self):", "def pop(self):", '"""Простой стек."""'):
            self.assertIn(kept, text)

    def test_new_method_appended_inside_class(self):
        patch = "class Stack:\n    def peek(self):\n        return self.items[-1]\n"
        text = merge_source(STACK, patch)
        ns: dict = {}
        exec(compile(text, "mylib.py", "exec"), ns)
        s = ns["Stack"]()
        s.push(5)
        self.assertEqual(s.peek(), 5)
        self.assertIn("def helper():", text)

    def test_two_space_indent_of_original_kept(self):
        orig = "class C:\n  def f(self):\n    return 1\n\n  def g(self):\n    return 2\n"
        text = merge_source(orig, "class C:\n    def f(self):\n        return 10\n")
        # сдвигается база метода; внутренний отступ тела остаётся своим — это корректный Python
        self.assertTrue(text.startswith("class C:\n  def f(self):\n"))
        self.assertTrue(text.endswith("\n\n  def g(self):\n    return 2\n"))
        ns: dict = {}
        exec(compile(text, "mylib.py", "exec"), ns)
        self.assertEqual((ns["C"]().f(), ns["C"]().g()), (10, 2))

    def test_nested_class_merged_by_members(self):
        orig = "class Outer:\n    class Inner:\n        def a(self):\n            return 1\n\n" \
               "        def b(self):\n            return 2\n"
        text = merge_source(orig, "class Outer:\n    class Inner:\n        def a(self):\n            return 9\n")
        self.assertIn("return 9", text)
        self.assertIn("def b(self):\n            return 2", text)

    def test_lone_method_of_nested_class(self):
        orig = "class Outer:\n    class Inner:\n        def a(self):\n            return 1\n"
        text = merge_source(orig, "def a(self):\n    return 9\n")
        self.assertEqual(text, "class Outer:\n    class Inner:\n        def a(self):\n            return 9\n")

    def test_same_method_names_in_two_classes_merged_by_class(self):
        text = merge_source(TWO_CLASSES, "class B:\n    def push(self, x):\n        return 20\n")
        self.assertIn("return 1\n", text)
        self.assertIn("return 20\n", text)
        self.assertNotIn("return 2\n", text)


class Decorators(unittest.TestCase):
    ORIG = (
        "class Box:\n"
        "    def __init__(self, v):\n        self._v = v\n\n"
        "    @property\n    def v(self):\n        return self._v\n\n"
        "    @classmethod\n    def make(cls):\n        return cls(0)\n\n"
        "    @staticmethod\n    def unit():\n        return 0\n\n"
        "    async def load(self):\n        return None\n"
    )

    def test_property_replaced_with_decorator_once(self):
        text = merge_source(self.ORIG, "@property\ndef v(self):\n    return self._v * 2\n")
        self.assertEqual(text.count("@property"), 1)
        self.assertIn("    @property\n    def v(self):\n        return self._v * 2\n", text)

    def test_classmethod_and_staticmethod_lone(self):
        text = merge_source(self.ORIG, "@classmethod\ndef make(cls):\n    return cls(1)\n")
        self.assertIn("        return cls(1)", text)
        text = merge_source(self.ORIG, "@staticmethod\ndef unit():\n    return 1\n")
        self.assertIn("    @staticmethod\n    def unit():\n        return 1\n", text)
        self.assertEqual(text.count("def unit"), 1)

    def test_async_method_lone(self):
        text = merge_source(self.ORIG, "async def load(self):\n    return 42\n")
        self.assertIn("    async def load(self):\n        return 42\n", text)

    def test_plain_function_with_method_name_stays_top_level(self):
        # без self/cls и без декоратора метода — новая функция модуля, метод не трогаем
        text = merge_source(self.ORIG, "def unit():\n    return 5\n")
        self.assertIn("        return 0\n", text)
        self.assertTrue(text.endswith("\n\n\ndef unit():\n    return 5\n"))

    def test_crlf_file_keeps_crlf(self):
        orig = STACK.replace("\n", "\r\n")
        text = merge_source(orig, PUSH_FIXED)
        self.assertNotIn("\n", text.replace("\r\n", ""))
        self.assertIn("append(x)", text)


class GuessBlock(unittest.TestCase):
    def test_unheaded_lone_method_is_patch_of_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            (ws / "mylib.py").write_bytes(STACK.encode())
            (ws / "test_mylib.py").write_bytes(b"import mylib\n")
            blocks = find_patch("Fix:\n```python\n" + PUSH_FIXED + "```", ws)
            self.assertIsNotNone(blocks)
            self.assertTrue(PatchApplier().apply(blocks, ws).ok)
            self.assertIn("append(x)", (ws / "mylib.py").read_bytes().decode())

    def test_ambiguity_is_patch_error(self):
        with self.assertRaises(PatchError):
            merge_source(TWO_CLASSES, PUSH_FIXED)


if __name__ == "__main__":
    unittest.main()
