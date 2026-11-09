"""Разбор имён колёс в tools/check_wheels (spec spark-build, шаг 6). Офлайн; имена — настоящие файлы с PyPI."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.check_wheels import missing_arches, read_pins


class WheelTagsTest(unittest.TestCase):
    def test_pure_python_fits_both(self):
        self.assertEqual(missing_arches(["anyio-4.16.0-py3-none-any.whl", "anyio-4.16.0.tar.gz"]), [])

    def test_binary_needs_both_arches(self):
        files = ["numpy-2.5.4-cp312-cp312-manylinux_2_27_aarch64.manylinux_2_28_aarch64.whl",
                 "numpy-2.5.4-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl"]
        self.assertEqual(missing_arches(files), [])
        self.assertEqual(missing_arches(files[1:]), ["aarch64"])
        self.assertEqual(missing_arches(files[:1]), ["x86_64"])

    def test_abi3_older_python_fits(self):
        files = ["cryptography-50.0.2-cp311-abi3-manylinux2014_aarch64.manylinux_2_17_aarch64.whl",
                 "cryptography-50.0.2-cp311-abi3-manylinux2014_x86_64.manylinux_2_17_x86_64.whl"]
        self.assertEqual(missing_arches(files), [])

    def test_rejected_near_misses(self):
        # другой Python, musl, macOS и только исходники — не годятся для python:3.12-slim (glibc)
        for files in (["pandas-3.1.1-cp313-cp313-manylinux_2_28_aarch64.whl",
                       "pandas-3.1.1-cp313-cp313-manylinux_2_28_x86_64.whl"],
                      ["orjson-3.13.0-cp312-cp312-musllinux_1_2_aarch64.whl",
                       "orjson-3.13.0-cp312-cp312-musllinux_1_2_x86_64.whl"],
                      ["lxml-6.1.3-cp312-cp312-macosx_11_0_arm64.whl"],
                      ["somepkg-1.0.tar.gz"]):
            with self.subTest(files=files):
                self.assertEqual(missing_arches(files), ["x86_64", "aarch64"])

    def test_read_pins_skips_comments(self):
        with tempfile.TemporaryDirectory() as d:
            lock = Path(d, "r.lock")
            lock.write_text("aiohttp==3.14.5\n    # via openjiuwen\nopenjiuwen==0.1.20.post1\n", encoding="utf-8")
            self.assertEqual(read_pins(lock), [("aiohttp", "3.14.5"), ("openjiuwen", "0.1.20.post1")])


if __name__ == "__main__":
    unittest.main()
