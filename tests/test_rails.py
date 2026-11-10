"""TokenBudgetRail на событиях вызова модели HarnessAgent (поддельный OpenAI-сервер).

Ожидания — из spec `specs/openjiuwen-agent/spec.md` (критерии A и пре-мортем): рельса видит каждый вызов, цена
списывается ровно один раз (рельсой, а при её сбое — шлюзом), при исчерпанном бюджете запрос не уходит.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from harness.agent import HarnessAgent, agent_card, build_agent  # первым: тихий openjiuwen
from harness.budget import BudgetLedger, TokenPricing
from harness.calllog import JsonlLog
from harness.llm import BudgetExhausted, LlmGateway
from harness.rails import TokenBudgetRail
from harness.settings import load_settings_raw
from tests.test_llm_usage import MESSAGES, FakeOpenAI

RAW = load_settings_raw(None)


class Runner:
    def __init__(self, gateway, ledger, log):
        self.gateway, self.ledger, self.log = gateway, ledger, log

    async def run(self, tasks):
        return []


class CountingRail(TokenBudgetRail):
    def __init__(self, ledger, log, fail_after=False):
        super().__init__(ledger, log)
        self.before = self.after = 0
        self.fail_after = fail_after

    async def before_model_call(self, ctx):
        self.before += 1
        await super().before_model_call(ctx)

    async def after_model_call(self, ctx):
        self.after += 1
        if self.fail_after:
            raise RuntimeError("rail broke")
        await super().after_model_call(ctx)


class RailTest(unittest.TestCase):
    def setUp(self):
        self.srv = FakeOpenAI()
        self.addCleanup(self.srv.close)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.log_path = Path(tmp.name) / "run.jsonl"

    def setup_agent(self, budget=100_000, rail_cls=None, budget_rail=True, **rail_kw):
        ledger, log = BudgetLedger(budget), JsonlLog(self.log_path)
        llm = dict(RAW["llm"], timeout_sec=10, rate_limit_retries=0, network_retry_wait_sec=0)
        acc = RAW["accounting"]
        gateway = LlmGateway(self.srv.base_url, "test-key", llm,
                             TokenPricing(acc["input_weight"], acc["reasoning_in_completion"]), ledger, log, {})
        runner = Runner(gateway, ledger, log)
        if budget_rail:
            agent = asyncio.run(build_agent(runner, agent_card(RAW["agent"])))
        else:
            agent = HarnessAgent(runner, agent_card(RAW["agent"]))
            gateway.hooks = agent
        rail = None
        if rail_cls:
            rail = rail_cls(ledger, log, **rail_kw)
            asyncio.run(agent.register_rail(rail))
        return gateway, ledger, rail

    def ask(self, gateway, n=1):
        async def go():
            return [await gateway.ask("m", MESSAGES, task_id=f"t{i}", stage="stage1") for i in range(n)]
        return asyncio.run(go())

    def records(self, kind):
        return [r for r in map(json.loads, self.log_path.read_text(encoding="utf-8").splitlines()) if r["kind"] == kind]

    def test_rail_sees_each_call_and_charges_once(self):
        gateway, ledger, rail = self.setup_agent(rail_cls=CountingRail)
        replies = self.ask(gateway, 3)
        self.assertEqual((rail.before, rail.after), (3, 3))
        self.assertAlmostEqual(ledger.spent, sum(r.cost for r in replies))
        self.assertEqual(len(self.records("llm_call")), 3)

    def test_gateway_charges_when_rail_fails(self):
        gateway, ledger, rail = self.setup_agent(rail_cls=CountingRail, budget_rail=False, fail_after=True)
        # рельса учёта упала после вызова: списывает шлюз, вызов не падает
        replies = self.ask(gateway, 2)
        self.assertEqual(rail.after, 2)
        self.assertAlmostEqual(ledger.spent, sum(r.cost for r in replies))
        self.assertEqual(len(self.records("llm_call")), 2)

    def test_two_budget_rails_do_not_double_charge(self):
        gateway, ledger, rail = self.setup_agent(rail_cls=CountingRail)
        reply = self.ask(gateway)[0]
        self.assertAlmostEqual(ledger.spent, reply.cost)

    def test_exhausted_budget_stops_before_request(self):
        gateway, ledger, rail = self.setup_agent(budget=10, rail_cls=CountingRail)
        ledger.charge(10)
        with self.assertRaises(BudgetExhausted):
            self.ask(gateway)
        self.assertEqual(self.srv.bodies, [])
        self.assertEqual(rail.after, 0)

    def test_without_agent_gateway_still_charges(self):
        ledger, log = BudgetLedger(100_000), JsonlLog(self.log_path)
        llm = dict(RAW["llm"], timeout_sec=10, rate_limit_retries=0, network_retry_wait_sec=0)
        acc = RAW["accounting"]
        gateway = LlmGateway(self.srv.base_url, "test-key", llm,
                             TokenPricing(acc["input_weight"], acc["reasoning_in_completion"]), ledger, log, {})
        reply = self.ask(gateway)[0]
        self.assertAlmostEqual(ledger.spent, reply.cost)
        self.assertEqual(len(self.records("llm_call")), 1)


if __name__ == "__main__":
    unittest.main()
