"""Контракт короткого system: spec short-task-prompts; остальные сообщения прежние."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from harness.prompting import Stage1Prompt
from harness.settings import load_prompt, load_settings_raw
from harness.tasks import Task


class PromptContract(unittest.IsolatedAsyncioTestCase):
    async def test_short_system_preserves_other_messages(self):
        cfg = load_settings_raw()["stage1"]
        for marker in (None, "UNSOLVABLE"):
            args = (AsyncMock(build=AsyncMock(return_value="context")), marker,
                    cfg["tail_prompts"], cfg["patch_types"], cfg["patch_prompts"])
            old = Stage1Prompt(*args)
            new = Stage1Prompt(*args, system_prompts=cfg["system_prompts"])
            with tempfile.TemporaryDirectory() as tmp:
                for kind in ("math", "data_analysis", "search", "coding", "unknown"):
                    task = Task("t", kind, "question", 1, "exact", Path(tmp))
                    before, after = await old.build(task), await new.build(task)
                    self.assertEqual(before.messages[1], after.messages[1])
                    self.assertEqual(old.retry("error", kind), new.retry("error", kind))
                    if kind in ("coding", "unknown"):
                        self.assertEqual(before.messages, after.messages)
                    else:
                        expected = load_prompt("system_task_short.txt")
                        if marker:
                            expected = expected.rstrip("\n") + "\n" + load_prompt("system_unsolvable.txt").format(marker=marker)
                        self.assertEqual(after.messages[0]["content"], expected)
                        self.assertLess(after.prompt_chars, before.prompt_chars)
                        tail = load_prompt(cfg["tail_prompts"][kind]).strip()
                        self.assertTrue(after.messages[1]["content"].endswith(tail))


if __name__ == "__main__":
    unittest.main()
