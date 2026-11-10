"""HarnessAgent (наследник openjiuwen BaseAgent) против поддельного OpenAI-сервера.

Ожидания — из spec `specs/openjiuwen-agent/spec.md` (критерии A): прогон через `agent.invoke` даёт те же статусы
и стоимость, что `Harness.run`; события агента видны зарегистрированной рельсе; `only` сужает набор задач.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from harness.agent import HarnessAgent, agent_card, build_agent  # первым: тихий openjiuwen
from openjiuwen.core.single_agent.base import BaseAgent
from openjiuwen.core.single_agent.rail.base import AgentRail
from harness.settings import Settings, load_settings_raw
from harness.tasks import load_tasks
from run_agent import Harness
from tests.test_llm_usage import FakeOpenAI

SCRIPT = "```python\nprint(6 * 7)\n```"
# вопросы не по шаблонам этапа 0 — идут в LLM
QUESTIONS = {
    "math_a1": "A farmer plants 6 rows with 7 trees in each row. How many trees are planted?",
    "math_a2": "Six crates hold seven melons apiece. Count the melons altogether.",
    "math_a3": "There are 7 teams of 6 players at a tournament. How many players are there?",
}


def write_tasks(root: Path) -> Path:
    for task_id, question in QUESTIONS.items():
        d = root / task_id
        d.mkdir(parents=True)
        (d / "task.json").write_text(json.dumps({
            "task_id": task_id, "task_type": "math", "reward": 1, "check": "exact_int",
            "instruction": question + " Write ONLY the final answer to the file answer.txt."}), encoding="utf-8")
    return root


class EventRail(AgentRail):
    def __init__(self):
        super().__init__()
        self.events: list[str] = []

    async def before_invoke(self, ctx):
        self.events.append("before_invoke")

    async def after_invoke(self, ctx):
        self.events.append(f"after_invoke:{len(ctx.inputs.result['results'])}")


class AgentRun(unittest.TestCase):
    def setUp(self):
        self.srv = FakeOpenAI()
        self.srv.content = SCRIPT
        self.addCleanup(self.srv.close)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.settings = Settings(raw=load_settings_raw(None), base_url=self.srv.base_url, api_key="test-key",
                                 out_path=None, log_dir=self.root / "logs", budget=100_000)

    def agent(self) -> HarnessAgent:
        return asyncio.run(build_agent(Harness(self.settings, None), agent_card(self.settings.section("agent"))))

    @staticmethod
    def summary(results: list[dict]) -> dict:
        return {r["task_id"]: (r["status"], r.get("cost"), r.get("answer")) for r in results}

    def test_agent_is_openjiuwen_agent(self):
        self.assertIsInstance(self.agent(), BaseAgent)

    def test_invoke_matches_harness_run(self):
        direct_dir, agent_dir = write_tasks(self.root / "direct"), write_tasks(self.root / "agent")
        harness = Harness(self.settings, None)
        direct = asyncio.run(harness.run(load_tasks(direct_dir)))
        agent = self.agent()
        via_agent = asyncio.run(agent.invoke({"tasks_dir": str(agent_dir)}))["results"]
        # рельса списала вместо шлюза, а не вместе с ним
        self.assertAlmostEqual(agent._runner.ledger.spent, harness.ledger.spent)
        self.assertEqual(self.summary(via_agent), self.summary(direct))
        self.assertEqual({r["status"] for r in via_agent}, {"verified"})
        self.assertTrue(all(r["cost"] > 0 for r in via_agent))  # путь LLM, а не этап 0
        self.assertEqual((agent_dir / "math_a1" / "answer.txt").read_text(encoding="utf-8").strip(), "42")

    def test_rail_sees_invoke_and_only_filters(self):
        agent = self.agent()
        rail = EventRail()
        asyncio.run(agent.register_rail(rail))
        out = asyncio.run(agent.invoke({"tasks_dir": str(write_tasks(self.root / "t")), "only": ["math_a2"]}))
        self.assertEqual([r["task_id"] for r in out["results"]], ["math_a2"])
        self.assertEqual(rail.events, ["before_invoke", "after_invoke:1"])


if __name__ == "__main__":
    unittest.main()
