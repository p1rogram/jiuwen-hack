"""Вызов модели через openjiuwen с учётом токенов в общем бюджете."""
from __future__ import annotations

import asyncio
import math
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import harness.jiuwen_logging  # noqa: F401  — до openjiuwen: тихая консоль
from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig

from harness.attempt_cost import charge_failed_attempt
from harness.budget import BudgetLedger, TokenPricing, Usage
from harness.calllog import JsonlLog
from harness.model_call import BudgetExhausted, ModelCall, ModelCallHooks, admit, settle
from harness.prompting import prompt_chars

RESET_RE = re.compile(r"Limit resets at: (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) UTC")


@dataclass(frozen=True)
class LlmReply:
    text: str
    reasoning: str
    usage: Usage
    cost: float
    latency_sec: float


class LlmGateway:
    def __init__(self, base_url: str, api_key: str, llm_cfg: dict[str, Any],
                 pricing: TokenPricing, ledger: BudgetLedger, log: JsonlLog,
                 model_map: dict[str, dict[str, Any]] | None = None, missing_usage_chars_per_token: float = 2.5):
        self._client_cfg = ModelClientConfig(
            client_provider="OpenAI", api_key=api_key, api_base=base_url,
            timeout=float(llm_cfg["timeout_sec"]), verify_ssl=True,
        )
        self._cfg = llm_cfg
        self._pricing = pricing
        self._ledger = ledger
        self._log = log
        self._models: dict[str, Model] = {}
        # логическое имя из config → имя на сервере стенда и поля тела запроса (профиль, например spark)
        self._model_map = model_map or {}
        self._missing_cpt = missing_usage_chars_per_token
        # прокси NeuralDeep: не более 3 параллельных запросов на ключ, иначе 429
        self._inflight = asyncio.Semaphore(int(llm_cfg["max_inflight"]))
        self._retries = int(llm_cfg["rate_limit_retries"])
        self._wait_min, self._wait_max = llm_cfg["rate_limit_wait_sec"]
        # Повторяем обрыв и 5xx; вход каждой ошибки оцениваем отдельно, таймаут не повторяем.
        self._net_retries = int(llm_cfg["network_retries"])
        self._net_wait = float(llm_cfg["network_retry_wait_sec"])
        self._net_markers = tuple(llm_cfg["network_error_markers"])
        # события вызова модели агента (HarnessAgent): допуск и списание делает его рельса TokenBudgetRail
        self.hooks: ModelCallHooks | None = None

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

    def _is_network_error(self, error: str) -> bool:
        return any(m in error for m in self._net_markers)

    async def _invoke(self, model: str, messages: list[dict[str, str]], max_tokens: int, call: ModelCall):
        # 429: повтор только после времени сброса — частые повторы прокси увеличивают паузу.
        rate_limited = net_failed = attempt = 0
        while True:
            async with self._inflight:
                client = self._model(model)
                extra = self._extra_body(model)
                admit(call, self._ledger)
                attempt += 1
                try:
                    return await client.invoke(messages, max_tokens=max_tokens, **extra)
                except (Exception, asyncio.CancelledError) as exc:
                    try:
                        charge_failed_attempt(call, attempt, self._missing_cpt,
                                              self._pricing, self._ledger, self._log)
                    except Exception as accounting_error:
                        if isinstance(exc, asyncio.CancelledError):
                            raise exc from accounting_error
                        raise
                    if isinstance(exc, asyncio.CancelledError):
                        raise
                    failure = exc
            error = str(failure)
            wait = self._rate_limit_wait(error)
            if wait is not None and rate_limited < self._retries:
                rate_limited += 1
                self._log.write({"kind": "rate_limit", "task_id": call.task_id, "model": model,
                                 "wait_sec": round(wait, 1)})
            elif wait is None and self._is_network_error(error) and net_failed < self._net_retries:
                net_failed += 1
                wait = self._net_wait
                self._log.write({"kind": "net_retry", "task_id": call.task_id, "model": model,
                                 "wait_sec": wait, "error": error[:300]})
            else:
                raise failure
            if self._ledger.remaining <= 0:
                raise BudgetExhausted("budget exhausted") from failure
            await asyncio.sleep(wait)

    def served_name(self, name: str) -> str:
        return self._model_map.get(name, {}).get("model", name)

    def _extra_body(self, name: str) -> dict[str, Any]:
        extra = self._model_map.get(name, {}).get("extra_body")
        return {"extra_body": dict(extra)} if extra else {}

    def _model(self, name: str) -> Model:
        if name not in self._models:
            req = ModelRequestConfig(model=self.served_name(name), temperature=self._cfg["temperature"],
                                     max_tokens=self._cfg["max_output_tokens"])
            self._models[name] = Model(self._client_cfg, req)
        return self._models[name]

    def _usage(self, meta: Any, messages: list[dict[str, str]], text: str, reasoning: str) -> tuple[Usage, bool]:
        """Токены из ответа сервера. Нет usage или в нём 0 там, где текст был (промпт, ответ), — эта часть
        оценивается по символам с запасом: списать ноль за оплаченное нельзя, иначе бюджет ничего не ограничивает."""
        cpt = float(self._missing_cpt)
        missing = meta is None
        inp = int(getattr(meta, "input_tokens", 0) or 0)
        out = int(getattr(meta, "output_tokens", 0) or 0)
        reasoning_tokens = int(getattr(meta, "reasoning_tokens", 0) or 0)
        if inp == 0 and prompt_chars(messages):
            inp, missing = math.ceil(prompt_chars(messages) / cpt), True
        if out == 0 and (text or reasoning):
            out, reasoning_tokens, missing = math.ceil((len(text) + len(reasoning)) / cpt), 0, True
        return Usage(input_tokens=inp, output_tokens=out, reasoning_tokens=reasoning_tokens), missing

    async def _fire(self, event: str, call: ModelCall) -> None:
        """Событие агента; сбой обработчика не роняет вызов модели — допуск и списание шлюз тогда сделает сам."""
        if self.hooks is None:
            return
        try:
            await getattr(self.hooks, event)(call)
        except BudgetExhausted:
            raise
        except Exception as exc:
            self._log.write({"kind": "hook_error", "task_id": call.task_id, "event": event,
                             "error": f"{type(exc).__name__}: {exc}"[:300]})

    async def ask(self, model: str, messages: list[dict[str, str]], *, task_id: str, stage: str,
                  max_tokens: int | None = None) -> LlmReply:
        call = ModelCall(messages=messages, task_id=task_id, stage=stage, model=model,
                         served_model=self.served_name(model) if model in self._model_map else None,
                         prompt_chars=prompt_chars(messages))
        await self._fire("before_model_call", call)
        if not call.admitted:
            admit(call, self._ledger)
        started = time.monotonic()
        msg = await self._invoke(model, messages, max_tokens or self._cfg["max_output_tokens"], call)
        latency = time.monotonic() - started
        text = msg.content if isinstance(msg.content, str) else str(msg.content or "")
        reasoning = msg.reasoning_content or ""
        usage, missing = self._usage(msg.usage_metadata, messages, text, reasoning)
        cost = self._pricing.cost(usage)
        reply = LlmReply(text=text, reasoning=reasoning, usage=usage, cost=cost, latency_sec=latency)
        call.response, call.usage, call.cost, call.latency_sec = reply, usage, cost, latency
        call.reasoning_chars, call.content_chars, call.usage_missing = len(reasoning), len(text), missing
        await self._fire("after_model_call", call)
        if not call.charged:
            settle(call, self._ledger, self._log)
        return reply
