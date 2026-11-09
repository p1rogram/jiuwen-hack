"""Хвост сообщения задачи по типу (spec token-cuts, шаг 3): короткий выход у search/math/data_analysis, coding без хвоста.

Ожидания — из spec `specs/token-cuts/spec.md` (шаг 3): coding не трогаем — комментарии там служат рассуждением.
"""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.prompting import Stage1Prompt  # noqa: E402
from harness.settings import load_prompt, load_settings_raw  # noqa: E402
from harness.tasks import Task  # noqa: E402


class FakeSnapshot:
    async def build(self, task):
        return "FILES: none"


def user_text(task_type: str, tails) -> str:
    prompt = Stage1Prompt(FakeSnapshot(), None, tails)
    task = Task("t", task_type, "How many?", 1, "exact_int", Path("."))
    return asyncio.run(prompt.build(task)).messages[1]["content"]


class PromptTail(unittest.TestCase):
    tails = load_settings_raw()["stage1"]["tail_prompts"]

    def test_tail_ends_message_for_configured_type(self):
        for t in ("search", "math", "data_analysis"):
            with self.subTest(t=t):
                self.assertTrue(user_text(t, self.tails).endswith(load_prompt(self.tails[t]).strip()))

    def test_coding_has_no_tail(self):
        # должно быть отклонено: тип без хвоста получает сообщение как до шага 3
        self.assertEqual(user_text("coding", self.tails), user_text("coding", {}))
        self.assertNotIn("comments", user_text("coding", self.tails))

    def test_only_search_may_print_answer_from_context(self):
        # data/math видят лишь часть данных (5 строк CSV) — ответ «из контекста» там был бы догадкой
        self.assertIn("CONTEXT", user_text("search", self.tails))
        self.assertNotIn("CONTEXT", load_prompt(self.tails["data_analysis"]))
        self.assertNotIn("CONTEXT", load_prompt(self.tails["math"]))


if __name__ == "__main__":
    unittest.main()
