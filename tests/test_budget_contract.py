"""Контракт переменного бюджета организатора, дополнение spec от 13.11.2026."""
import os
import unittest
from unittest.mock import patch
from harness.settings import load_settings, parse_budget

class BudgetContractTest(unittest.TestCase):
    def test_invalid_fails_closed(self):
        for value in ('', ' ', '-1', 'nan', 'inf', 'lots'):
            with self.subTest(value=value), self.assertRaises(SystemExit):
                parse_budget(value, 100000)

    def test_zero_and_fraction(self):
        self.assertEqual(parse_budget('0', 100000), 0)
        self.assertEqual(parse_budget('0.5', 100000), .5)

    def test_precedence_and_default(self):
        with patch('harness.settings._dotenv_values', return_value={}), patch.dict(os.environ, {'LLM_API_KEY': 'test', 'BUDGET': '17', 'TOKEN_BUDGET': '88'}, clear=True):
            self.assertEqual(load_settings().budget, 17)
            self.assertEqual(load_settings(budget=3).budget, 3)
            self.assertEqual(load_settings(budget=0).budget, 0)
            os.environ['BUDGET'] = 'bad'
            self.assertEqual(load_settings(budget=3).budget, 3)
            del os.environ['BUDGET']
            self.assertEqual(load_settings().budget, 88)
            del os.environ['TOKEN_BUDGET']
            self.assertEqual(load_settings().budget, load_settings().raw['budget_default'])

    def test_cli_validation(self):
        with patch('harness.settings._dotenv_values', return_value={}), patch.dict(os.environ, {'LLM_API_KEY': 'test'}, clear=True):
            for value in (-1, float('inf'), float('nan')):
                with self.subTest(value=value), self.assertRaises(SystemExit):
                    load_settings(budget=value)

    def test_container_forwards_resolved_budget_and_zero(self):
        from tools.docker_run import container_env
        from types import SimpleNamespace
        settings = SimpleNamespace(api_key='test', base_url='http://localhost', profile=None, budget=17.5)
        self.assertEqual(container_env(settings, None)['BUDGET'], '17.5')
        self.assertEqual(container_env(settings, '0')['BUDGET'], '0')
