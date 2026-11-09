"""LlmGateway против поддельного OpenAI-сервера (spec spark-build, шаги 2–3).

Ожидания: формула учёта — harness/budget.py (выходные + вход × input_weight, контракт регламента); формат ответа —
OpenAI Chat Completions (usage.prompt_tokens / completion_tokens); имена и поля тела — tests/fixtures/profiles/stand.json.
"""
from __future__ import annotations

import asyncio
import json
import math
import tempfile
import threading
import unittest
from unittest import mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from harness.budget import BudgetLedger, TokenPricing
from harness.calllog import JsonlLog
from harness.llm import LlmGateway
from harness.prompting import prompt_chars
from harness import settings
from harness.settings import load_settings_raw

FIXTURES = Path(__file__).parent / "fixtures" / "profiles"  # профиль стенда для тестов; настоящие профили — локальные
with mock.patch.object(settings, "PROFILES_DIR", FIXTURES):
    STAND = load_settings_raw("stand")
LOGICAL = STAND["models"]["stage1"]
CONTENT = "print(42)\n" * 20
MESSAGES = [{"role": "user", "content": "write a script"}]


class FakeOpenAI:
    """Отвечает на /v1/chat/completions; запоминает тела запросов; usage — по флагу."""

    def __init__(self):
        self.bodies: list[dict] = []
        self.usage: dict | None = {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}
        self.fail_codes: list[int] = []  # коды ошибок для первых запросов, по одному на запрос
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.bodies.append(body)
                if owner.fail_codes:
                    code = owner.fail_codes.pop(0)
                    data = json.dumps({"error": {"message": f"fake {code}", "type": "server_error"}}).encode()
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                reply = {"id": "c1", "object": "chat.completion", "created": 0, "model": body.get("model"),
                         "choices": [{"index": 0, "finish_reason": "stop",
                                      "message": {"role": "assistant", "content": CONTENT}}]}
                if owner.usage is not None:
                    reply["usage"] = owner.usage
                data = json.dumps(reply).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class GatewayTest(unittest.TestCase):
    def setUp(self):
        self.srv = FakeOpenAI()
        self.addCleanup(self.srv.close)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log_path = Path(self.tmp.name) / "run.jsonl"
        self.ledger = BudgetLedger(100_000)

    def gateway(self, model_map):
        llm = dict(STAND["llm"], timeout_sec=10, rate_limit_retries=0, network_retry_wait_sec=0)
        acc = STAND["accounting"]
        return LlmGateway(self.srv.base_url, "test-key", llm,
                          TokenPricing(acc["input_weight"], acc["reasoning_in_completion"]),
                          self.ledger, JsonlLog(self.log_path), model_map)

    def ask(self, gw, model=LOGICAL):
        return asyncio.run(gw.ask(model, MESSAGES, task_id="t1",
                                  stage="stage1"))

    def calls(self):
        return [json.loads(line) for line in self.log_path.read_text(encoding="utf-8").splitlines()]

    def test_model_map_sends_served_name_and_extra_body(self):
        self.ask(self.gateway(STAND["model_map"]))
        body = self.srv.bodies[-1]
        self.assertEqual(body["model"], "served/qwen3.6-35b-a3b")
        self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": False})
        rec = self.calls()[-1]
        self.assertEqual((rec["model"], rec["served_model"]), (LOGICAL, "served/qwen3.6-35b-a3b"))

    def test_without_map_name_passes_unchanged(self):
        self.ask(self.gateway({}))
        body = self.srv.bodies[-1]
        self.assertEqual(body["model"], LOGICAL)
        self.assertNotIn("chat_template_kwargs", body)
        self.assertNotIn("served_model", self.calls()[-1])

    def test_usage_charged_exactly(self):
        reply = self.ask(self.gateway(STAND["model_map"]))
        self.assertAlmostEqual(reply.cost, 20 + 100 * STAND["accounting"]["input_weight"])
        self.assertAlmostEqual(self.ledger.spent, reply.cost)
        self.assertNotIn("usage_missing", self.calls()[-1])

    def test_missing_usage_is_not_free(self):
        self.srv.usage = None
        reply = self.ask(self.gateway(STAND["model_map"]))
        cpt = STAND["accounting"]["missing_usage_chars_per_token"]
        w = STAND["accounting"]["input_weight"]
        expected = math.ceil(len(CONTENT) / cpt) + math.ceil(prompt_chars(MESSAGES) / cpt) * w  # spec: обе части
        self.assertAlmostEqual(reply.cost, expected)
        self.assertAlmostEqual(self.ledger.spent, reply.cost)
        self.assertTrue(self.calls()[-1]["usage_missing"])

    def test_zero_completion_with_content_is_estimated(self):
        self.srv.usage = {"prompt_tokens": 100, "completion_tokens": 0, "total_tokens": 100}
        reply = self.ask(self.gateway({}))
        self.assertGreater(reply.cost, 0)
        self.assertTrue(self.calls()[-1]["usage_missing"])

    def test_zero_prompt_tokens_estimated(self):
        self.srv.usage = {"prompt_tokens": 0, "completion_tokens": 20, "total_tokens": 20}
        reply = self.ask(self.gateway({}))
        cpt = STAND["accounting"]["missing_usage_chars_per_token"]
        w = STAND["accounting"]["input_weight"]
        self.assertAlmostEqual(reply.cost, 20 + math.ceil(prompt_chars(MESSAGES) / cpt) * w)
        self.assertTrue(self.calls()[-1]["usage_missing"])

    def test_server_error_is_retried_without_charge(self):
        # должно пройти (spec robustness, шаг 4): 500, затем 200 → ответ получен, списан один вызов
        self.srv.fail_codes = [500]
        reply = self.ask(self.gateway({}))
        self.assertEqual(reply.text, CONTENT)
        self.assertEqual(len(self.srv.bodies), 2)
        self.assertAlmostEqual(self.ledger.spent, reply.cost)
        kinds = [r["kind"] for r in self.calls()]
        self.assertEqual(kinds, ["net_retry", "llm_call"])

    def test_retries_are_limited(self):
        self.srv.fail_codes = [502] * 10
        with self.assertRaises(Exception):
            self.ask(self.gateway({}))
        self.assertEqual(len(self.srv.bodies), 1 + STAND["llm"]["network_retries"])
        self.assertEqual(self.ledger.spent, 0)

    def test_connection_refused_is_retried(self):
        self.srv.close()  # порт свободен — соединение отклоняется до ответа сервера
        with self.assertRaises(Exception):
            self.ask(self.gateway({}))
        retries = [r for r in self.calls() if r["kind"] == "net_retry"]
        self.assertEqual(len(retries), STAND["llm"]["network_retries"])
        self.assertIn("APIConnectionError", retries[0]["error"])
        self.assertEqual(self.ledger.spent, 0)

    def test_client_error_is_not_retried(self):
        # должно быть отклонено: 400 — ошибка запроса, повтор ничего не даст
        self.srv.fail_codes = [400]
        with self.assertRaises(Exception):
            self.ask(self.gateway({}))
        self.assertEqual(len(self.srv.bodies), 1)
        self.assertEqual(self.calls() if self.log_path.exists() else [], [])


if __name__ == "__main__":
    unittest.main()
