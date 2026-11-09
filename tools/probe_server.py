"""Проверка OpenAI-совместимого сервера перед прогоном: какие модели есть, отдаёт ли он usage и входят ли
рассуждения в completion_tokens (→ accounting.reasoning_in_completion).

Только стандартная библиотека: запускается системным python3 на стенде, без venv и openjiuwen.
Запуск: python3 tools/probe_server.py [--base-url URL] [--model NAME] [--key-env LLM_API_KEY] [--out report.json]
Ключ берётся из env по имени и не печатается.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any

DEFAULT_BASE_URL = "http://127.0.0.1:22500/v1"
QUESTION = "What is 17 * 23? Think briefly, then answer with the number only."
# граница «символов на токен»: у обычного текста 1–8; больше — счётчик токенов не включает этот текст
MAX_CHARS_PER_TOKEN = 8.0


class ProbeError(RuntimeError):
    pass


def _request(url: str, key: str, timeout: float, body: dict | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise ProbeError(f"{url}: HTTP {exc.code} {exc.read()[:300].decode(errors='replace')}") from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise ProbeError(f"{url}: {exc}") from None


def list_models(base_url: str, key: str, timeout: float = 10.0) -> list[str]:
    data = _request(f"{base_url.rstrip('/')}/models", key, timeout)
    return [m["id"] for m in data.get("data", []) if isinstance(m, dict) and "id" in m]


def ask(base_url: str, key: str, model: str, thinking: bool, timeout: float) -> dict[str, Any]:
    body = {"model": model, "temperature": 0.0, "max_tokens": 1024,
            "messages": [{"role": "user", "content": QUESTION}],
            "chat_template_kwargs": {"enable_thinking": thinking}}
    started = time.monotonic()
    data = _request(f"{base_url.rstrip('/')}/chat/completions", key, timeout, body)
    msg = (data.get("choices") or [{}])[0].get("message") or {}
    usage = data.get("usage")
    details = (usage or {}).get("completion_tokens_details") or {}
    reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""
    return {"thinking": thinking, "latency_sec": round(time.monotonic() - started, 2),
            "usage": usage is not None,
            "prompt_tokens": (usage or {}).get("prompt_tokens"),
            "completion_tokens": (usage or {}).get("completion_tokens"),
            "reasoning_tokens_field": details.get("reasoning_tokens"),
            "content_chars": len(msg.get("content") or ""), "reasoning_chars": len(reasoning),
            "content": (msg.get("content") or "")[:80]}


def reasoning_in_completion(r: dict[str, Any]) -> bool | None:
    """True — рассуждения посчитаны в completion_tokens, False — нет, None — не определить (нет usage или рассуждений)."""
    if not r["usage"] or not r["reasoning_chars"] or not r["completion_tokens"]:
        return None
    if r["reasoning_tokens_field"]:
        content_tokens = r["completion_tokens"] - r["reasoning_tokens_field"]
        return content_tokens >= 0 and content_tokens <= max(r["content_chars"], 1)
    total_cpt = (r["content_chars"] + r["reasoning_chars"]) / r["completion_tokens"]
    content_cpt = r["content_chars"] / r["completion_tokens"]
    if total_cpt <= MAX_CHARS_PER_TOKEN:
        return True
    if content_cpt <= MAX_CHARS_PER_TOKEN:
        return False
    return None


def probe(base_url: str, key: str, model: str | None, timeout: float) -> dict[str, Any]:
    models = list_models(base_url, key, timeout)
    chosen = model or (models[0] if models else None)
    if not chosen:
        raise ProbeError("server lists no models")
    runs = [ask(base_url, key, chosen, thinking, timeout) for thinking in (False, True)]
    verdict = next((v for v in (reasoning_in_completion(r) for r in runs) if v is not None), None)
    return {"base_url": base_url, "models": models, "model": chosen, "model_listed": chosen in models,
            "runs": runs, "usage_ok": all(r["usage"] and r["completion_tokens"] for r in runs),
            "reasoning_in_completion": verdict}


def report_lines(rep: dict[str, Any]) -> list[str]:
    lines = [f"server: {rep['base_url']}", f"models: {', '.join(rep['models']) or '-'}",
             f"model: {rep['model']}" + ("" if rep["model_listed"] else "  (NOT in /models)")]
    for r in rep["runs"]:
        lines.append(f"  thinking={r['thinking']!s:5}  usage={r['usage']!s:5}  prompt={r['prompt_tokens']}  "
                     f"completion={r['completion_tokens']}  reasoning_field={r['reasoning_tokens_field']}  "
                     f"content={r['content_chars']} ch  reasoning={r['reasoning_chars']} ch  {r['latency_sec']} s")
    lines.append("usage: ok" if rep["usage_ok"] else
                 "usage: MISSING — budget will be estimated from characters (usage_missing in the log)")
    v = rep["reasoning_in_completion"]
    lines.append("accounting.reasoning_in_completion: " +
                 ("unknown (no reasoning text in replies)" if v is None else f"{str(v).lower()}"))
    return lines


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default=os.environ.get("LLM_BASE_URL") or DEFAULT_BASE_URL)
    p.add_argument("--model", default=None, help="имя на сервере; по умолчанию первое из /models")
    p.add_argument("--key-env", default="LLM_API_KEY", help="имя переменной окружения с ключом (может быть пустой)")
    p.add_argument("--timeout", type=float, default=120.0)
    p.add_argument("--out", default=None, help="JSON-отчёт")
    args = p.parse_args()
    try:
        rep = probe(args.base_url, os.environ.get(args.key_env, ""), args.model, args.timeout)
    except ProbeError as exc:
        print(f"probe failed: {exc}", file=sys.stderr)
        return 2
    print("\n".join(report_lines(rep)))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=2)
    return 0 if rep["usage_ok"] and rep["model_listed"] else 1


if __name__ == "__main__":
    sys.exit(main())
