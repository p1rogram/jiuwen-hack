"""Вклейка ответа-правки. Ожидаемое поведение — из specs/coding-patch/spec.md (п. 1), не из кода."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.patching import PatchApplier, PatchBlock, merge_source, parse_blocks  # noqa: E402

# форма модуля задач: только функции по 2–5 строк (шаг 0 spec, пример coding_0003)
ORIGINAL = (
    "def reverse_words(s):\n"
    "    return ' '.join(sorted(s.split()))\n"
    "\n\n"
    "def total(xs):\n"
    "    s = 0\n"
    "    for x in xs:\n"
    "        s -= x\n"
    "    return s\n"
    "\n\n"
    "def first_n(xs, n):\n"
    "    return xs[:n]\n"
)
FIXED_TOTAL = "def total(xs):\n    # bug: subtracted instead of adding\n    return sum(xs)\n"
REPLY = "The bug is in total.\n```python\n# file: mylib.py\n" + FIXED_TOTAL + "```\n"


class Workspace:
    def __enter__(self) -> Path:
        self._tmp = tempfile.TemporaryDirectory()
        ws = Path(self._tmp.name)
        (ws / "mylib.py").write_bytes(ORIGINAL.encode())
        return ws

    def __exit__(self, *exc):
        self._tmp.cleanup()


class KeyPair(unittest.TestCase):
    def test_only_changed_function_is_replaced(self):
        with Workspace() as ws:
            run = PatchApplier().apply(parse_blocks(REPLY), ws)
            self.assertTrue(run.ok, run.stderr)
            text = (ws / "mylib.py").read_bytes().decode()
        self.assertIn(FIXED_TOTAL, text)
        self.assertNotIn("s -= x", text)
        # две другие функции байт-в-байт как были
        self.assertTrue(text.startswith("def reverse_words(s):\n    return ' '.join(sorted(s.split()))\n\n\n"))
        self.assertTrue(text.endswith("\n\n\ndef first_n(xs, n):\n    return xs[:n]\n"))

    def test_syntax_error_rejected_file_untouched(self):
        reply = "```python\n# file: mylib.py\ndef total(xs):\n    return sum(xs\n```"
        with Workspace() as ws:
            run = PatchApplier().apply(parse_blocks(reply), ws)
            self.assertEqual((ws / "mylib.py").read_bytes().decode(), ORIGINAL)
        self.assertFalse(run.ok)
        self.assertIn("syntax error", run.stderr)

    def test_path_outside_rejected_file_untouched(self):
        reply = "```python\n# file: ../other/mylib.py\n" + FIXED_TOTAL + "```"
        with Workspace() as ws:
            run = PatchApplier().apply(parse_blocks(reply), ws)
            self.assertEqual((ws / "mylib.py").read_bytes().decode(), ORIGINAL)
            self.assertFalse((ws.parent / "other").exists())
        self.assertFalse(run.ok)
        self.assertIn("outside the task folder", run.stderr)


class Rules(unittest.TestCase):
    def test_reply_without_file_header_is_a_script(self):
        self.assertIsNone(parse_blocks("```python\nprint('DONE')\n```"))

    def test_header_line_before_block(self):
        # проба 2026-11-09 (coding_0002, coding_0008): модель пишет заголовок строкой перед ```python
        reply = "Need to fix total.\n\n# file: mylib.py\n\n```python\n" + FIXED_TOTAL + "```"
        self.assertEqual(parse_blocks(reply), [PatchBlock("mylib.py", FIXED_TOTAL)])
        self.assertEqual(parse_blocks("`# file: mylib.py`\n```python\n" + FIXED_TOTAL + "```")[0].path, "mylib.py")

    def test_unfenced_code_after_header(self):
        # проба 2026-11-10 (coding_0008): код без ```-разметки после заголовка
        reply = "The module is missing.\n# file: mylib.py\n" + FIXED_TOTAL
        self.assertEqual(parse_blocks(reply), [PatchBlock("mylib.py", FIXED_TOTAL)])
        self.assertIsNone(parse_blocks("def total(xs):\n    return sum(xs)\n"))

    def test_header_before_earlier_block_not_reused(self):
        reply = "# file: mylib.py\n```python\n" + FIXED_TOTAL + "```\nCheck:\n```python\nprint(1)\n```"
        self.assertEqual(len(parse_blocks(reply)), 1)

    def test_missing_file_written_whole(self):
        # missing_lib: модуля нет, модель пишет его целиком
        with Workspace() as ws:
            (ws / "mylib.py").unlink()
            run = PatchApplier().apply(parse_blocks(REPLY), ws)
            self.assertTrue(run.ok, run.stderr)
            self.assertEqual((ws / "mylib.py").read_bytes().decode(), FIXED_TOTAL)

    def test_test_file_and_absolute_path_rejected(self):
        for path in ("test_mylib.py", "C:/x/mylib.py", "/tmp/mylib.py"):
            with Workspace() as ws:
                run = PatchApplier().apply([PatchBlock(path, FIXED_TOTAL)], ws)
                self.assertFalse(run.ok, path)

    def test_whole_file_reply_replaces_every_function(self):
        whole = ORIGINAL.replace("s -= x", "s += x")
        self.assertEqual(merge_source(ORIGINAL, whole), whole)

    def test_new_function_appended_and_import_added(self):
        patch = "import math\n\ndef area(r):\n    return math.pi * r * r\n"
        text = merge_source(ORIGINAL, patch)
        self.assertTrue(text.startswith("import math\n"))
        self.assertTrue(text.endswith("\n\n\ndef area(r):\n    return math.pi * r * r\n"))
        self.assertEqual(merge_source(text, "import math\n").count("import math"), 1)

    def test_decorated_function_replaced_with_decorator(self):
        orig = "import functools\n\n@functools.lru_cache\ndef f(x):\n    return x\n\n\ndef g():\n    return 1\n"
        patch = "@functools.lru_cache(maxsize=None)\ndef f(x):\n    return x + 1\n"
        text = merge_source(orig, patch)
        self.assertEqual(text.count("lru_cache"), 1)
        self.assertIn("return x + 1", text)
        self.assertIn("def g():\n    return 1\n", text)

    def test_crlf_file_keeps_crlf(self):
        orig = ORIGINAL.replace("\n", "\r\n")
        text = merge_source(orig, FIXED_TOTAL)
        self.assertNotIn("\n", text.replace("\r\n", ""))

    def test_two_blocks_same_file_both_applied(self):
        reply = REPLY + "```python\n# file: mylib.py\ndef first_n(xs, n):\n    return list(xs)[:n]\n```"
        with Workspace() as ws:
            self.assertTrue(PatchApplier().apply(parse_blocks(reply), ws).ok)
            text = (ws / "mylib.py").read_text()
        self.assertIn("return sum(xs)", text)
        self.assertIn("list(xs)[:n]", text)


if __name__ == "__main__":
    unittest.main()
