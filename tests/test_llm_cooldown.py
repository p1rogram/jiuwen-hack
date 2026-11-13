"""Ожидания конкурентности и учёта: specs/shared-429-cooldown/spec.md."""
import asyncio
import math
import unittest
from types import SimpleNamespace
from unittest import mock

from harness.budget import BudgetLedger, TokenPricing
from harness.cooldown import SharedCooldown
from harness.llm import LlmGateway
from harness.model_call import BudgetExhausted, ModelCall
from harness.prompting import prompt_chars
from harness.settings import load_settings_raw


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleepers = []

    def __call__(self):
        return self.now

    async def sleep(self, delay):
        future = asyncio.get_running_loop().create_future()
        self.sleepers.append((self.now + delay, future))
        await future

    def advance(self, now):
        self.now = now
        for deadline, future in self.sleepers:
            if deadline <= now and not future.done():
                future.set_result(None)


class Log:
    def __init__(self):
        self.records = []

    def write(self, record):
        self.records.append(record)


class Client:
    def __init__(self, clock):
        self.clock = clock
        self.calls = []
        self.pending = []

    async def invoke(self, messages, **kwargs):
        self.calls.append((self.clock(), messages, kwargs))
        future = asyncio.get_running_loop().create_future()
        self.pending.append(future)
        return await future


def reply():
    return SimpleNamespace(content='ok', reasoning_content='',
                           usage_metadata=SimpleNamespace(input_tokens=100, output_tokens=20,
                                                          reasoning_tokens=0))


class CooldownTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.clock = Clock()
        self.log = Log()
        self.ledger = BudgetLedger(100000)
        cfg = dict(load_settings_raw()['llm'], shared_429_cooldown=True,
                   rate_limit_retries=0, rate_limit_wait_sec=[30, 30])
        self.gateway = LlmGateway('http://localhost/v1', 'test', cfg,
                                  TokenPricing(0.1, True), self.ledger, self.log)
        self.gateway._cooldown = SharedCooldown(True, clock=self.clock, sleep=self.clock.sleep)
        self.client = Client(self.clock)
        self.gateway._model = mock.Mock(return_value=self.client)
        self.tasks = []

    async def asyncTearDown(self):
        for task in self.tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def pump(self):
        for _ in range(12):
            await asyncio.sleep(0)

    def start(self, task_id, model='alias'):
        task = asyncio.create_task(self.gateway.ask(model, [{'role': 'user', 'content': task_id}],
                                                      task_id=task_id, stage='stage1.1', max_tokens=17))
        self.tasks.append(task)
        return task

    async def test_terminal_429_blocks_queued_alias_but_active_calls_complete(self):
        first, second, third = [self.start(str(i)) for i in range(3)]
        await self.pump()
        queued = self.start('queued', model='other-alias')
        await self.pump()
        self.assertEqual(len(self.client.calls), 3)
        self.client.pending[0].set_exception(RuntimeError('429'))
        await self.pump()
        with self.assertRaisesRegex(RuntimeError, '429'):
            await first
        self.assertEqual(len(self.client.calls), 3)
        self.clock.advance(1)
        self.client.pending[1].set_result(reply())
        self.client.pending[2].set_result(reply())
        await self.pump()
        self.assertTrue(second.done() and third.done())
        self.assertEqual(len(self.client.calls), 3)
        self.clock.advance(29.999)
        await self.pump()
        self.assertEqual(len(self.client.calls), 3)
        self.clock.advance(30)
        await self.pump()
        self.assertEqual(self.client.calls[3][0], 30)
        self.client.pending[3].set_result(reply())
        await queued

    async def test_later_429_extends_deadline_and_success_does_not_reset(self):
        first, second, third = [self.start(str(i)) for i in range(3)]
        await self.pump()
        self.client.pending[0].set_exception(RuntimeError('429'))
        await self.pump()
        self.clock.advance(10)
        self.client.pending[1].set_exception(RuntimeError('429'))
        self.client.pending[2].set_result(reply())
        await self.pump()
        queued = self.start('queued')
        self.clock.advance(30)
        await self.pump()
        self.assertEqual(len(self.client.calls), 3)
        self.clock.advance(39.999)
        await self.pump()
        self.assertEqual(len(self.client.calls), 3)
        self.clock.advance(40)
        await self.pump()
        self.assertEqual(self.client.calls[3][0], 40)
        self.client.pending[3].set_result(reply())
        await queued
        await asyncio.gather(first, second, third, return_exceptions=True)

    async def test_waiters_do_not_hold_slots_and_cancellation_is_local(self):
        self.gateway._cooldown.extend(30)
        cancelled = self.start('cancelled')
        surviving = self.start('surviving')
        await self.pump()
        await self.gateway._inflight.acquire()
        self.gateway._inflight.release()
        cancelled.cancel()
        await self.pump()
        self.assertFalse(surviving.done())
        self.assertEqual(self.ledger.spent, 0)
        self.assertEqual(self.client.calls, [])
        self.clock.advance(30)
        await self.pump()
        self.assertEqual(len(self.client.calls), 1)
        self.client.pending[0].set_result(reply())
        await surviving

    async def test_budget_exhausted_during_wait_prevents_send(self):
        self.gateway._cooldown.extend(30)
        task = self.start('waiting')
        await self.pump()
        self.ledger.charge(self.ledger.total)
        self.clock.advance(30)
        await self.pump()
        with self.assertRaises(BudgetExhausted):
            await task
        self.assertEqual(self.client.calls, [])
        self.assertEqual(self.ledger.spent, self.ledger.total)

    async def test_recheck_after_semaphore_acquisition(self):
        self.gateway._inflight = asyncio.Semaphore(1)
        await self.gateway._inflight.acquire()
        task = self.start('waiting-slot')
        await self.pump()
        self.gateway._cooldown.extend(30)
        self.gateway._inflight.release()
        await self.pump()
        self.assertEqual(self.client.calls, [])
        self.clock.advance(30)
        await self.pump()
        self.assertEqual(len(self.client.calls), 1)
        self.client.pending[0].set_result(reply())
        await task

    async def test_cooldown_published_even_if_error_accounting_log_fails(self):
        first = self.start('first')
        await self.pump()
        original = self.log.write
        def write(record):
            if record['kind'] == 'llm_attempt_error':
                raise OSError('disk full')
            original(record)
        self.log.write = write
        self.client.pending[0].set_exception(RuntimeError('429'))
        await self.pump()
        self.assertEqual(self.gateway._cooldown.remaining(), 30)
        self.assertTrue(first.done())
        self.assertEqual(len(self.client.calls), 1)

    async def test_no_429_payload_and_usage_equal_with_flag_disabled(self):
        results = []
        for enabled in (False, True):
            self.gateway._cooldown = SharedCooldown(enabled, clock=self.clock, sleep=self.clock.sleep)
            self.gateway._model_map = {'alias': {'extra_body': {'x': 1}}}
            task = self.start('same')
            await self.pump()
            self.client.pending[-1].set_result(reply())
            results.append(await task)
        self.assertEqual(self.client.calls[0][1:], self.client.calls[1][1:])
        self.assertEqual(self.client.calls[0][2], {'max_tokens': 17, 'extra_body': {'x': 1}})
        self.assertEqual([r.cost for r in results], [30, 30])
        self.assertEqual(self.ledger.spent, 60)

    async def test_error_and_success_charged_once_without_wait_charges(self):
        self.gateway._retries = 1
        task = self.start('charged')
        await self.pump()
        self.client.pending[0].set_exception(RuntimeError('429'))
        await self.pump()
        error_cost = math.ceil(prompt_chars([{'role': 'user', 'content': 'charged'}]) / 2.5) * 0.1
        self.assertAlmostEqual(self.ledger.spent, error_cost)
        self.clock.advance(30)
        await self.pump()
        self.assertEqual(len(self.client.calls), 2)
        self.client.pending[1].set_result(reply())
        await task
        self.assertAlmostEqual(self.ledger.spent, error_cost + 30)
        self.assertEqual(sum(r['kind'] == 'llm_attempt_error' for r in self.log.records), 1)
        self.assertEqual(sum(r['kind'] == 'llm_call' for r in self.log.records), 1)

    async def test_non_429_error_does_not_start_cooldown(self):
        task = self.start('bad-request')
        await self.pump()
        self.client.pending[0].set_exception(RuntimeError('400 invalid request'))
        await self.pump()
        with self.assertRaisesRegex(RuntimeError, '400'):
            await task
        self.assertEqual(self.gateway._cooldown.remaining(), 0)
        another = self.start('valid')
        await self.pump()
        self.assertEqual(self.client.calls[-1][0], 0)
        self.client.pending[-1].set_result(reply())
        await another

    async def test_new_telemetry_failure_does_not_repeat_success(self):
        original = self.log.write
        def write(record):
            if record['kind'] in ('llm_attempt_start', 'llm_attempt_end'):
                raise OSError('disk full')
            original(record)
        self.log.write = write
        task = self.start('success')
        await self.pump()
        self.client.pending[0].set_result(reply())
        result = await task
        self.assertEqual(result.cost, 30)
        self.assertEqual(self.ledger.spent, 30)
        self.assertEqual(len(self.client.calls), 1)

    async def test_attempt_trace_correlates_terminal_429_without_payload(self):
        task = self.start('request-payload')
        await self.pump()
        self.client.pending[0].set_exception(RuntimeError('429 secret-error-text'))
        await self.pump()
        with self.assertRaisesRegex(RuntimeError, '429'):
            await task
        records = [r for r in self.log.records if r['kind'] in ('llm_attempt_start', 'llm_attempt_end')]
        self.assertEqual([r['kind'] for r in records], ['llm_attempt_start', 'llm_attempt_end'])
        self.assertEqual(records[0]['call_id'], records[1]['call_id'])
        self.assertEqual([r['attempt'] for r in records], [1, 1])
        self.assertEqual(records[1]['error_class'], 'RuntimeError')
        self.assertEqual(records[1]['cooldown_deadline'], 30)
        self.assertEqual(records[1]['rate_limit_wait_sec'], 30)
        for record in records:
            self.assertNotIn('messages', record)
            self.assertNotIn('error', record)
            self.assertNotIn('secret-error-text', str(record))

    async def test_network_then_429_use_separate_retry_counters_and_costs(self):
        self.gateway._net_retries = 1
        self.gateway._retries = 1
        self.gateway._net_wait = 5
        task = self.start('mixed-errors')
        await self.pump()
        self.client.pending[0].set_exception(RuntimeError('APIConnectionError'))
        await self.pump()
        self.assertEqual(self.gateway._cooldown.remaining(), 0)
        self.clock.advance(5)
        await self.pump()
        self.assertEqual(len(self.client.calls), 2)
        self.client.pending[1].set_exception(RuntimeError('429'))
        await self.pump()
        self.clock.advance(34.999)
        await self.pump()
        self.assertEqual(len(self.client.calls), 2)
        self.clock.advance(35)
        await self.pump()
        self.assertEqual([call[0] for call in self.client.calls], [0, 5, 35])
        self.client.pending[2].set_result(reply())
        await task
        error_cost = math.ceil(prompt_chars([{'role': 'user', 'content': 'mixed-errors'}]) / 2.5) * 0.1
        self.assertAlmostEqual(self.ledger.spent, 30 + 2 * error_cost)
        self.assertEqual(sum(r['kind'] == 'net_retry' for r in self.log.records), 1)
        self.assertEqual(sum(r['kind'] == 'rate_limit' for r in self.log.records), 1)
        self.assertEqual(sum(r['kind'] == 'llm_attempt_error' for r in self.log.records), 2)
        self.assertEqual(sum(r['kind'] == 'llm_call' for r in self.log.records), 1)

    async def test_trace_distinguishes_gate_wait_from_slot_wait(self):
        self.gateway._inflight = asyncio.Semaphore(1)
        await self.gateway._inflight.acquire()
        self.gateway._cooldown.extend(30)
        task = self.start('wait-both')
        await self.pump()
        self.clock.advance(30)
        await self.pump()
        self.assertEqual(self.client.calls, [])
        self.clock.advance(40)
        self.gateway._inflight.release()
        await self.pump()
        self.assertEqual(self.client.calls[0][0], 40)
        record = next(r for r in self.log.records if r['kind'] == 'llm_attempt_start')
        self.assertEqual(record['gate_wait_sec'], 30)
        self.assertEqual(record['gate_and_slot_wait_sec'], 40)
        self.assertEqual(record['gate_and_slot_wait_sec'] - record['gate_wait_sec'], 10)
        self.client.pending[0].set_result(reply())
        await task

    async def test_malformed_reset_still_charges_and_publishes_cooldown(self):
        task = self.start('malformed-reset')
        await self.pump()
        self.client.pending[0].set_exception(RuntimeError('429 Limit resets at: 2026-99-11 00:00:00 UTC'))
        await self.pump()
        expected_cost = math.ceil(prompt_chars([{'role': 'user', 'content': 'malformed-reset'}]) / 2.5) * 0.1
        self.assertAlmostEqual(self.ledger.spent, expected_cost)
        self.assertEqual(self.gateway._cooldown.remaining(), 30)
        with self.assertRaisesRegex(RuntimeError, '429'):
            await task
        self.assertEqual(len(self.client.calls), 1)
        self.assertEqual(sum(r['kind'] == 'llm_attempt_error' for r in self.log.records), 1)
        self.assertEqual(sum(r['kind'] == 'llm_attempt_end' for r in self.log.records), 1)
