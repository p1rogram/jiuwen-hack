"""Тихий openjiuwen: его журнал только в файл (./logs/logs/run/jiuwen.log от текущей папки), в stdout — ничего.

Импортировать до первого импорта openjiuwen.core.foundation.llm: логгеры создаются при импорте пакета.
"""
from __future__ import annotations

import copy
import warnings

from harness.settings import load_config

# ключи выводов в конфиге журнала openjiuwen (default/constant.py)
_OUTPUT_KEYS = ("output", "interface_output", "performance_output")
_done = False


def silence() -> None:
    global _done
    if _done:
        return
    cfg = load_config("console.json")["openjiuwen"]
    # pysbd импортируется лениво при первом вызове модели и печатает SyntaxWarning в stderr
    for module in cfg["ignore_syntax_warnings_from"]:
        warnings.filterwarnings("ignore", category=SyntaxWarning, module=rf".*{module}")
    from openjiuwen.core.common.logging.default.constant import DEFAULT_INNER_LOG_CONFIG
    from openjiuwen.core.common.logging.log_config import configure_log_config

    # ключи напрямую, без обёртки {"logging": ...} — с ней ValidationError
    log_cfg = copy.deepcopy(DEFAULT_INNER_LOG_CONFIG)
    for key in _OUTPUT_KEYS:
        log_cfg[key] = list(cfg["outputs"])
    log_cfg["propagate"] = False  # чужой обработчик корневого логгера вернул бы строки в консоль
    configure_log_config(log_cfg)
    _done = True


silence()
