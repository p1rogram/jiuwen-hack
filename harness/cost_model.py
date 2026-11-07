"""Оценка расхода задачи в выходных токенах по готовому промпту и типу; поправка по факту во время прогона."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Estimate:
    mean: float
    high: float


@dataclass
class _TypeStats:
    chars_per_token: float
    out_mean: float
    out_p90: float
    p_retry: float
    obs_sum: float = 0.0
    obs_n: int = 0
    in_ratio_sum: float = 0.0
    in_ratio_n: int = 0


class CostModel:
    """Параметры — config/cost_model.json (пишет tools/calibrate_cost.py), ключ этапа — stage1."""

    def __init__(self, cfg: dict[str, Any], input_weight: float, stage: str = "stage1"):
        c = cfg[stage]
        self._w = input_weight
        self._retry_chars = float(c["retry_prompt_chars"])
        self._prior = float(c["prior_weight"])
        # символов на токен зависит от содержимого: search (имена, kb) ~2.8, math (английский текст) ~3.9
        self._types = {t: _TypeStats(float(s["chars_per_token"]), float(s["out_mean"]), float(s["out_p90"]),
                                     float(s["p_retry"]))
                       for t, s in c["types"].items()}

    def _stats(self, task_type: str) -> _TypeStats:
        return self._types.get(task_type) or self._types["default"]

    def input_tokens(self, task_type: str, prompt_chars: int) -> float:
        s = self._stats(task_type)
        # масштаб по типу: символов на токен свой у каждого типа, общий множитель сдвигал бы чужие оценки
        return prompt_chars / s.chars_per_token * (self._prior + s.in_ratio_sum) / (self._prior + s.in_ratio_n)

    def _out_scale(self, s: _TypeStats) -> float:
        mean = (self._prior * s.out_mean + s.obs_sum) / (self._prior + s.obs_n)
        return mean / s.out_mean if s.out_mean > 0 else 1.0

    def estimate(self, task_type: str, prompt_chars: int) -> Estimate:
        s = self._stats(task_type)
        scale = self._out_scale(s)
        inp = self.input_tokens(task_type, prompt_chars)
        retry_in = self._retry_chars / s.chars_per_token

        def cost(out: float) -> float:
            first = self._w * inp + out
            # повтор: тот же контекст + ответ модели + текст ошибки, и выход ещё раз
            second = self._w * (inp + out + retry_in) + out
            return first + s.p_retry * second

        return Estimate(mean=cost(s.out_mean * scale), high=cost(s.out_p90 * scale))

    def observe(self, task_type: str, prompt_chars: int, input_tokens: int, output_tokens: int) -> None:
        """Факт первого вызова задачи: сдвигает выход типа и масштаб входа к наблюдаемым."""
        s = self._stats(task_type)
        s.obs_sum += output_tokens
        s.obs_n += 1
        predicted = prompt_chars / s.chars_per_token
        if predicted > 0 and input_tokens > 0:
            s.in_ratio_sum += input_tokens / predicted
            s.in_ratio_n += 1
