"""Контракт specs/api-attempt-cost: 25 символов / 2,5 × 0,1 = 1 за ошибку."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from harness.budget import BudgetLedger, TokenPricing
from harness.llm import LlmGateway
from harness.model_call import BudgetExhausted, settle
from harness.settings import load_settings_raw


class AttemptCostTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        cfg = dict(load_settings_raw()["llm"], network_retry_wait_sec=0,
                   rate_limit_wait_sec=[0, 0])
        self.ledger = BudgetLedger(100)
        self.log = Mock()
        self.gw = LlmGateway("http://127.0.0.1:1", "test", cfg,
                             TokenPricing(.1, True), self.ledger, self.log)
        self.client = Mock(invoke=AsyncMock())
        self.gw._model = Mock(return_value=self.client)
        self.messages = [{"role": "user", "content": "x" * 25}]
        self.reply = SimpleNamespace(content="ok", reasoning_content="",
                                     usage_metadata=SimpleNamespace(input_tokens=100, output_tokens=20))

    async def ask(self, task="t"):
        return await self.gw.ask("m", self.messages, task_id=task, stage="stage1")

    def errors(self):
        return [c.args[0] for c in self.log.write.call_args_list
                if c.args[0]["kind"] == "llm_attempt_error"]

    async def test_failure_then_success(self):
        self.ledger.reserve("t", 50)
        self.client.invoke.side_effect = [RuntimeError("InternalServerError"), self.reply]
        reply = await self.ask()
        self.assertEqual((self.ledger.spent, reply.cost, self.ledger.available), (31, 30, 50))
        event = self.errors()[0]
        self.assertEqual((event["attempt"], event["input_tokens"], event["cost"]), (1, 10, 1))
        self.assertTrue(event["usage_estimated"])
        self.assertEqual(event["estimate_scope"], "input_only")
        self.assertNotIn("output_tokens", event)
        self.assertNotIn("error", event)

    async def test_terminal_errors(self):
        for error, count in [("400 secret body", 1), ("timeout", 1),
                             ("APIConnectionError", 3), ("InternalServerError", 3), ("429", 4)]:
            with self.subTest(error=error):
                self.setUp()
                self.client.invoke.side_effect = RuntimeError(error)
                with self.assertRaisesRegex(RuntimeError, error):
                    await self.ask()
                self.assertEqual(self.ledger.spent, count)
                self.assertEqual(len(self.errors()), count)
                self.assertEqual([e["attempt"] for e in self.errors()], list(range(1, count + 1)))

    async def test_local_model_failure_is_free(self):
        self.gw._model.side_effect = ValueError("bad model")
        with self.assertRaises(ValueError):
            await self.ask()
        self.assertEqual(self.ledger.spent, 0)
        self.assertEqual(self.errors(), [])

    async def test_concurrent_failures_keep_task_identity(self):
        arrived, release = asyncio.Event(), asyncio.Event()
        count = 0
        async def invoke(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 2:
                arrived.set()
            await release.wait()
            raise RuntimeError("400")
        self.client.invoke.side_effect = invoke
        self.gw._model_map = {"m": {"model": "served"}}
        a, b = asyncio.create_task(self.ask("a")), asyncio.create_task(self.ask("b"))
        await asyncio.wait_for(arrived.wait(), 2)
        release.set()
        result = await asyncio.gather(a, b, return_exceptions=True)
        self.assertTrue(all(isinstance(r, RuntimeError) for r in result))
        self.assertEqual(self.ledger.spent, 2)
        self.assertEqual({e["task_id"] for e in self.errors()}, {"a", "b"})
        self.assertEqual([e["served_model"] for e in self.errors()], ["served", "served"])

    async def test_exhaustion_stops_retry(self):
        self.ledger.total = 1
        self.client.invoke.side_effect = RuntimeError("InternalServerError")
        with self.assertRaises(BudgetExhausted):
            await self.ask()
        self.assertEqual(self.client.invoke.await_count, 1)
        self.assertEqual(self.ledger.spent, 1)

    async def test_cancellation_in_invoke_charged_once(self):
        self.client.invoke.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.ask()
        self.assertEqual(self.ledger.spent, 1)
        self.assertEqual(len(self.errors()), 1)

    async def test_journal_failure_does_not_retry(self):
        self.client.invoke.side_effect = RuntimeError("InternalServerError")
        self.log.write.side_effect = OSError("disk full")
        with self.assertRaises(OSError):
            await self.ask()
        self.assertEqual(self.client.invoke.await_count, 1)
        self.assertEqual(self.ledger.spent, 1)

    async def test_journal_failure_preserves_cancellation(self):
        self.client.invoke.side_effect = asyncio.CancelledError()
        self.log.write.side_effect = OSError("disk full")
        with self.assertRaises(asyncio.CancelledError) as raised:
            await self.ask()
        self.assertIsInstance(raised.exception.__cause__, OSError)
        self.assertEqual(self.client.invoke.await_count, 1)
        self.assertEqual(self.ledger.spent, 1)

    async def test_waiting_request_rechecks_budget(self):
        entered, release = asyncio.Event(), asyncio.Event()
        self.gw._inflight = asyncio.Semaphore(1)
        async def invoke(*args, **kwargs):
            entered.set()
            await release.wait()
            return self.reply
        self.client.invoke.side_effect = invoke
        self.ledger.total = 30
        first = asyncio.create_task(self.ask("a"))
        await entered.wait()
        second = asyncio.create_task(self.ask("b"))
        await asyncio.sleep(0)
        release.set()
        await first
        with self.assertRaises(BudgetExhausted):
            await second
        self.assertEqual(self.client.invoke.await_count, 1)

    async def test_cancelled_waiter_is_free(self):
        await self.gw._inflight.acquire()
        # Занимаем все места, чтобы отмена случилась до Model.invoke.
        while not self.gw._inflight.locked():
            await self.gw._inflight.acquire()
        task = asyncio.create_task(self.ask())
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.ledger.spent, 0)
        self.assertEqual(self.errors(), [])

    async def test_hooks_do_not_double_charge(self):
        for broken in [False, True]:
            with self.subTest(broken=broken):
                self.setUp()
                async def after(call):
                    settle(call, self.ledger, self.log)
                    if broken:
                        raise RuntimeError("hook failed after charge")
                self.gw.hooks = SimpleNamespace(before_model_call=AsyncMock(), after_model_call=after)
                self.client.invoke.side_effect = [RuntimeError("InternalServerError"), self.reply]
                await self.ask()
                self.assertEqual(self.ledger.spent, 31)
                self.assertEqual(len(self.errors()), 1)
