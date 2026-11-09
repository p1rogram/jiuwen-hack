"""Проверка requirements.lock: каждая закреплённая версия ставится готовым колесом на linux x86_64 и aarch64
(cp312). Жюри собирает Dockerfile на своей машине, архитектура неизвестна; в slim-образе нет компилятора.

Нужна сеть (PyPI JSON API). Запуск: python tools/check_wheels.py [requirements.lock] [--python cp312]
Код выхода 1 — есть пакеты без колеса под одну из архитектур (список печатается).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ARCHES = ("x86_64", "aarch64")
PIN_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;#]+)")


def read_pins(lock: Path) -> list[tuple[str, str]]:
    pins = []
    for line in lock.read_text(encoding="utf-8").splitlines():
        m = PIN_RE.match(line.strip())
        if m:
            pins.append((m.group(1), m.group(2)))
    return pins


def _wheel_fits(filename: str, arch: str, python: str) -> bool:
    # имя колеса: name-ver(-build)?-pytag-abitag-plattag.whl; теги могут быть составными через точку
    parts = filename[:-4].split("-")
    if len(parts) < 5:
        return False
    py_tags, abi_tags, plat_tags = parts[-3].split("."), parts[-2].split("."), parts[-1].split(".")
    plat_ok = any(t == "any" or (t.startswith("manylinux") and t.endswith(arch)) for t in plat_tags)
    py_ok = any(t == python or t.startswith("py3") or (t.startswith("cp3") and "abi3" in abi_tags
                                                      and int(t[3:] or 0) <= int(python[3:])) for t in py_tags)
    return plat_ok and py_ok


def missing_arches(files: list[str], python: str = "cp312") -> list[str]:
    """Архитектуры из ARCHES, под которые среди файлов релиза нет подходящего колеса."""
    wheels = [f for f in files if f.endswith(".whl")]
    return [a for a in ARCHES if not any(_wheel_fits(w, a, python) for w in wheels)]


def release_files(name: str, version: str, retries: int = 4) -> list[str]:
    err: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=60) as resp:
                return [u["filename"] for u in json.load(resp)["urls"]]
        except Exception as exc:  # сеть до PyPI бывает нестабильной: TLS-таймауты
            err = exc
            time.sleep(1 + attempt)
    raise RuntimeError(f"{name}=={version}: {err}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("lock", nargs="?", default=str(Path(__file__).resolve().parent.parent / "requirements.lock"))
    p.add_argument("--python", default="cp312")
    args = p.parse_args()
    pins = read_pins(Path(args.lock))
    with ThreadPoolExecutor(4) as ex:
        results = list(ex.map(lambda pin: (pin, missing_arches(release_files(*pin), args.python)), pins))
    bad = [(f"{n}=={v}", miss) for (n, v), miss in results if miss]
    for pin, miss in bad:
        print(f"no wheel for {', '.join(miss)}: {pin}")
    print(f"checked {len(pins)} pins: {len(pins) - len(bad)} ok, {len(bad)} without wheels")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
