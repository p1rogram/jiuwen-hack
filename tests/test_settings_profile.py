"""Профиль стенда поверх config/harness.json (spec spark-build, критерий «профиль»)."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from harness import settings

BASE = json.loads(settings.CONFIG_PATH.read_text(encoding="utf-8"))
KEY_NAMES = BASE["api_key_env_names"] + BASE["base_url_env_names"] + [settings.PROFILE_ENV]


def clean_env(**extra: str):
    env = {k: v for k, v in os.environ.items() if k not in KEY_NAMES}
    env.update(extra)
    return mock.patch.dict(os.environ, env, clear=True)


class ProfileMergeTest(unittest.TestCase):
    def test_no_profile_equals_harness_json(self):
        self.assertEqual(settings.load_settings_raw(), BASE)
        self.assertEqual(settings.load_settings_raw(None), BASE)

    def test_spark_overrides_only_its_keys(self):
        raw = settings.load_settings_raw("spark")
        self.assertEqual(raw["llm_base_url_default"], "http://127.0.0.1:22500/v1")
        self.assertEqual(raw["llm"]["max_inflight"], 4)
        self.assertTrue(raw["api_key_optional"])
        entry = raw["model_map"][BASE["models"]["stage1"]]
        self.assertEqual(entry["model"], "nvidia/Qwen3.6-35B-A3B-NVFP4")
        self.assertEqual(entry["extra_body"], {"chat_template_kwargs": {"enable_thinking": False}})
        # остальное — как в базе, включая соседние ключи вложенного раздела
        self.assertEqual({k: v for k, v in raw["llm"].items() if k != "max_inflight"},
                         {k: v for k, v in BASE["llm"].items() if k != "max_inflight"})
        changed = {"llm_base_url_default", "api_key_optional", "model_map", "llm"}
        self.assertEqual({k: v for k, v in raw.items() if k not in changed},
                         {k: v for k, v in BASE.items() if k not in changed})

    def test_unknown_key_rejected(self):
        with self.assertRaises(SystemExit) as cm:
            settings.merge_profile(BASE, {"llm": {"max_inflght": 4}})
        self.assertIn("max_inflght", str(cm.exception))
        with self.assertRaises(SystemExit):
            settings.merge_profile(BASE, {"budget_defualt": 1})

    def test_section_must_stay_object(self):
        with self.assertRaises(SystemExit):
            settings.merge_profile(BASE, {"llm": 4})


class ProfileResolveTest(unittest.TestCase):
    def test_bad_names_rejected(self):
        for name in ("../harness", "Spark", "spark/x", "..", "a" * 40):
            with self.subTest(name=name), self.assertRaises(SystemExit):
                settings.resolve_profile(name)

    def test_missing_profile_rejected(self):
        with self.assertRaises(SystemExit) as cm:
            settings.resolve_profile("nosuch")
        self.assertIn("spark", str(cm.exception))

    def test_env_used_when_arg_absent(self):
        with clean_env(HARNESS_PROFILE="spark"):
            self.assertEqual(settings.resolve_profile(None), "spark")
        with clean_env():
            self.assertIsNone(settings.resolve_profile(None))

    def test_profile_file_with_typo_fails_load(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "typo.json").write_text(json.dumps({"llm": {"max_inflght": 8}}), encoding="utf-8")
            with mock.patch.object(settings, "PROFILES_DIR", Path(d)), clean_env(LLM_API_KEY="k"), \
                    mock.patch.object(settings, "_dotenv_values", return_value={}):
                with self.assertRaises(SystemExit):
                    settings.load_settings(profile="typo")


class ApiKeyTest(unittest.TestCase):
    def setUp(self):
        self.no_dotenv = mock.patch.object(settings, "_dotenv_values", return_value={})
        self.no_dotenv.start()
        self.addCleanup(self.no_dotenv.stop)

    def test_spark_without_key_uses_placeholder(self):
        with clean_env():
            s = settings.load_settings(profile="spark")
        self.assertEqual(s.api_key, settings.NO_KEY_PLACEHOLDER)
        self.assertEqual(s.base_url, "http://127.0.0.1:22500/v1")
        self.assertEqual(s.profile, "spark")

    def test_no_profile_without_key_still_fails(self):
        with clean_env(), self.assertRaises(SystemExit):
            settings.load_settings()

    def test_real_key_wins_over_placeholder(self):
        with clean_env(LLM_API_KEY="laika-key"):
            s = settings.load_settings(profile="spark")
        self.assertEqual(s.api_key, "laika-key")


if __name__ == "__main__":
    unittest.main()
