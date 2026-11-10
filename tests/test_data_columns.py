"""data: объявленные столбцы списком в скобках и остановка по KeyError на объявленном отсутствующем столбце.

Ожидания — из specs/optimization-research/findings.md (идея 3): var2 data_analysis_v07 «In sales.csv
(order_id,region,product,month,units,price,discount)» — детектор не читал список в скобках, этап 1 падал с
`KeyError: 'discount'`, этап 2 тратил ≈ 600 токенов без ответа. Близкие случаи, которые обязаны остаться без
остановки: скобки не со столбцами, столбец выведен моделью из вопроса, CSV без шапки.
"""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.executor import RunResult  # noqa: E402
from harness.verifier import MISSING_COLUMN, SCRIPT_ERROR, Verifier  # noqa: E402
from tests.test_feasibility import Base, codes  # noqa: E402

SALES = "order_id,region,product,month,units,price\n1,north,widget,7,3,2.5\n2,south,gadget,8,1,4.0\n"
LISTED = "In sales.csv (order_id,region,product,month,units,price,discount) each row is one order. "
ASK_DISCOUNT = "What is the average discount of product 'widget'?"
KEY_ERROR = "Traceback (most recent call last):\n  ...\nKeyError: 'discount'\n"


class ParenthesizedColumns(Base):
    def test_listed_column_asked_but_missing_is_hard(self):
        task = self.make_task("data_analysis", LISTED + ASK_DISCOUNT, {"sales.csv": SALES})
        self.assertEqual(codes(self.detector.check(task)), ["missing_column"])

    def test_listed_columns_all_present_is_not_hard(self):
        text = LISTED.replace(",discount", "") + "What is the average price of product 'widget'?"
        task = self.make_task("data_analysis", text, {"sales.csv": SALES})
        self.assertEqual(codes(self.detector.check(task)), [])

    def test_parentheses_that_are_not_columns_are_ignored(self):
        # должно быть отклонено: в скобках не перечень столбцов (из шапки ни одного имени)
        text = "In sales.csv (north, south, east regions only) count orders. What is the average discount?"
        task = self.make_task("data_analysis", text, {"sales.csv": SALES})
        self.assertEqual(codes(self.detector.check(task)), [])


class KeyErrorStop(Base):
    def verdict(self, text, csv_body, stderr=KEY_ERROR, probe=True):
        task = self.make_task("data_analysis", text, {"sales.csv": csv_body})
        v = Verifier(500, column_probe=self.detector.missing_column if probe else None)
        return asyncio.run(v.check(task, RunResult(1, "", stderr, False)))

    def test_declared_missing_column_stops(self):
        self.assertEqual(self.verdict(LISTED + "Report the mean discount.", SALES).kind, MISSING_COLUMN)

    def test_column_derived_by_model_is_a_plain_script_error(self):
        # должно быть отклонено: «revenue» условие не объявляло — повтор ещё может посчитать units·price
        v = self.verdict(LISTED + "What is the total revenue?", SALES, "KeyError: 'revenue'\n")
        self.assertEqual(v.kind, SCRIPT_ERROR)

    def test_headerless_csv_is_a_plain_script_error(self):
        body = "1,north,widget,7,3,2.5,0.1\n2,south,gadget,8,1,4.0,0.0\n"
        self.assertEqual(self.verdict(LISTED + ASK_DISCOUNT, body).kind, SCRIPT_ERROR)

    def test_without_probe_kind_is_unchanged(self):
        self.assertEqual(self.verdict(LISTED + ASK_DISCOUNT, SALES, probe=False).kind, SCRIPT_ERROR)


if __name__ == "__main__":
    unittest.main()
