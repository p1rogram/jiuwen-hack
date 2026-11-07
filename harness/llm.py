"""Вызов модели через openjiuwen с учётом токенов в общем бюджете."""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig

from harness.budget import BudgetLedger, TokenPricing, Usage
from harness.calllog import JsonlLog
from harness.prompting import prompt_chars

RESET_RE = re.compile(r"Limit resets at: (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) UTC")


@dataclass(frozen=True)
class LlmReply:
    text: str
    reasoning: str
    usage: Usage
    cost: float
    latency_sec: float


class BudgetExhausted(RuntimeError):
    pass


class LlmGateway:
    def __init__(self, base_url: str, api_key: str, llm_cfg: dict[str, Any],
                 pricing: TokenPricing, ledger: BudgetLedger, log: JsonlLog):
        self._client_cfg = ModelClientConfig(
            client_provider="OpenAI", api_key=api_key, api_base=base_url,
            timeout=float(llm_cfg["timeout_sec"]), verify_ssl=True,
        )
        self._cfg = llm_cfg
        self._pricing = pricing
        self._ledger = ledger
        self._log = log
        self._models: dict[str, Model] = {}
        # прокси NeuralDeep: не более 3 параллельных запросов на ключ, иначе 429
        self._inflight = asyncio.Semaphore(int(llm_cfg["max_inflight"]))
        self._retries = int(llm_cfg["rate_limit_retries"])
        self._wait_min, self._wait_max = llm_cfg["rate_limit_wait_sec"]

    def _rate_limit_wait(self, error: str) -> float | None:
        if "429" not in error:
            return None
        m = RESET_RE.search(error)
        if m:
            reset = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            wait = (reset - datetime.now(timezone.utc)).total_seconds() + 0.5
        else:
            wait = self._wait_min
        return min(max(wait, self._wait_min), self._wait_max)

    async def _invoke(self, model: str, messages: list[dict[str, str]], max_tokens: int, task_id: str):
        # повтор только после времени сброса из ответа: частые повторы прокси наказывает паузой на 60 мин
        for attempt in range(self._retries + 1):
            try:
                async with self._inflight:
                    return await self._model(model).invoke(messages, max_tokens=max_tokens)
            except Exception as exc:
                wait = self._rate_limit_wait(str(exc))
                if wait is None or attempt == self._retries:
                    raise
                self._log.write({"kind": "rate_limit", "task_id": task_id, "model": model, "wait_sec": round(wait, 1)})
                await asyncio.sleep(wait)
        raise RuntimeError("unreachable")

    def _model(self, name: str) -> Model:
        if name not in self._models:
            req = ModelRequestConfig(model=name, temperature=self._cfg["temperature"],
                                     max_tokens=self._cfg["max_output_tokens"])
            self._models[name] = Model(self._client_cfg, req)
        return self._models[name]

    async def ask(self, model: str, messages: list[dict[str, str]], *, task_id: str, stage: str,
                  max_tokens: int | None = None) -> LlmReply:
        if self._ledger.remaining <= 0:
            raise BudgetExhausted("budget exhausted")
        started = time.monotonic()
        msg = await self._invoke(model, messages, max_tokens or self._cfg["max_output_tokens"], task_id)
        latency = time.monotonic() - started
        meta = msg.usage_metadata
        usage = Usage(
            input_tokens=int(getattr(meta, "input_tokens", 0) or 0),
            output_tokens=int(getattr(meta, "output_tokens", 0) or 0),
            reasoning_tokens=int(getattr(meta, "reasoning_tokens", 0) or 0),
        )
        cost = self._pricing.cost(usage)
        self._ledger.charge(cost, task_id)
        text = msg.content if isinstance(msg.content, str) else str(msg.content or "")
        reasoning = msg.reasoning_content or ""
        self._log.write({
            "kind": "llm_call", "task_id": task_id, "stage": stage, "model": model,
            "prompt_chars": prompt_chars(messages), "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
            "reasoning_tokens": usage.reasoning_tokens, "reasoning_chars": len(reasoning),
            "content_chars": len(text), "cost": round(cost, 1), "latency_sec": round(latency, 2),
            "budget_spent": round(self._ledger.spent, 1),
        })
        return LlmReply(text=text, reasoning=reasoning, usage=usage, cost=cost, latency_sec=latency)
