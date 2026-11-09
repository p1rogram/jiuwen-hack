"""tools/probe_server против поддельного сервера (spec spark-build, шаг 4).

Ожидания: формат OpenAI Chat Completions; смысл reasoning_in_completion — config/harness.json (accounting) и
harness/budget.py: True — рассуждения уже в completion_tokens, False — их нужно добавить.
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from tools import probe_server

KEY = "secret-laika-key-123"
REASONING = "Let me compute 17 * 23. 17 * 20 = 340, 17 * 3 = 51, total 391. " * 10  # ~640 символов
CONTENT = "391"


class FakeServer:
    def __init__(self, mode: str):
        self.mode = mode  # included | excluded | no_usage
        self.auth: list[str | None] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def _send(self, obj):
                data = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                owner.auth.append(self.headers.get("Authorization"))
                self._send({"object": "list", "data": [{"id": "nvidia/Qwen3.6-35B-A3B-NVFP4"}]})

            def do_POST(self):
                owner.auth.append(self.headers.get("Authorization"))
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                thinking = body["chat_template_kwargs"]["enable_thinking"]
                reasoning = REASONING if thinking else ""
                msg = {"role": "assistant", "content": CONTENT}
                if reasoning:
                    msg["reasoning_content"] = reasoning
                reply = {"choices": [{"index": 0, "message": msg, "finish_reason": "stop"}]}
                content_tokens, reasoning_tokens = 2, len(reasoning) // 4
                if owner.mode == "included":
                    reply["usage"] = {"prompt_tokens": 30, "completion_tokens": content_tokens + reasoning_tokens}
                elif owner.mode == "excluded":
                    reply["usage"] = {"prompt_tokens": 30, "completion_tokens": content_tokens}
                self._send(reply)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def run_main(base_url: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    argv = ["probe_server", "--base-url", base_url, "--key-env", "PROBE_TEST_KEY", "--timeout", "10"]
    with mock.patch.object(sys, "argv", argv), mock.patch.dict("os.environ", {"PROBE_TEST_KEY": KEY}), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = probe_server.main()
    return code, out.getvalue(), err.getvalue()


class ProbeTest(unittest.TestCase):
    def server(self, mode):
        srv = FakeServer(mode)
        self.addCleanup(srv.close)
        return srv

    def test_reasoning_counted_in_completion(self):
        srv = self.server("included")
        code, out, _ = run_main(srv.base_url)
        self.assertEqual(code, 0)
        self.assertIn("accounting.reasoning_in_completion: true", out)
        self.assertIn("usage: ok", out)
        self.assertIn("nvidia/Qwen3.6-35B-A3B-NVFP4", out)

    def test_reasoning_not_counted(self):
        srv = self.server("excluded")
        _, out, _ = run_main(srv.base_url)
        self.assertIn("accounting.reasoning_in_completion: false", out)

    def test_missing_usage_reported_and_fails(self):
        srv = self.server("no_usage")
        code, out, _ = run_main(srv.base_url)
        self.assertEqual(code, 1)
        self.assertIn("usage: MISSING", out)

    def test_key_sent_but_never_printed(self):
        srv = self.server("included")
        code, out, err = run_main(srv.base_url)
        self.assertTrue(srv.auth and all(a == f"Bearer {KEY}" for a in srv.auth))
        self.assertNotIn(KEY, out + err)

    def test_unreachable_server(self):
        code, _, err = run_main("http://127.0.0.1:9/v1")
        self.assertEqual(code, 2)
        self.assertIn("probe failed", err)


if __name__ == "__main__":
    unittest.main()
