"""Тихий openjiuwen (specs/console/spec.md): ни одной его строки в stdout, без SyntaxWarning pysbd в stderr,
файловый журнал ./logs/logs/run/jiuwen.log остаётся. Вызов модели — на закрытый порт: так срабатывают и ленивые импорты
(pysbd), и запись ERROR."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CALL = """
import asyncio, sys
sys.path.insert(0, {root!r})
{first}
from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
cfg = ModelClientConfig(client_provider="OpenAI", api_key="x", api_base="http://127.0.0.1:9/v1", timeout=2.0,
                        verify_ssl=True)
model = Model(cfg, ModelRequestConfig(model="m", temperature=0, max_tokens=5))
async def go():
    try:
        await model.invoke([{{"role": "user", "content": "hi"}}], max_tokens=5)
    except Exception:
        pass
asyncio.run(go())
"""


def run(first: str) -> tuple[str, str, Path]:
    cwd = Path(tempfile.mkdtemp())
    code = CALL.format(root=str(ROOT), first=first)
    p = subprocess.run([sys.executable, "-c", code], cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    return p.stdout, p.stderr, cwd


class QuietJiuwen(unittest.TestCase):
    def test_harness_import_silences_console(self):
        out, err, cwd = run("import harness.llm")
        self.assertEqual(out.strip(), "")
        self.assertNotIn("SyntaxWarning", err)
        self.assertTrue((cwd / "logs" / "logs" / "run" / "jiuwen.log").is_file())

    def test_without_harness_openjiuwen_prints(self):
        # контроль: тот же вызов без настройки печатает в stdout — иначе первый тест ничего бы не проверял
        out, _, _ = run("")
        self.assertGreaterEqual(len(out.strip().splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
