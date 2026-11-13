"""Единый допуск: spec evidence-safe-search, раздел 4."""
import unittest

from harness.outcome import Outcome
from harness.pipeline import TaskPipeline
from harness.verifier import BAD_FORMAT, Verdict


class RejectedCandidateContract(unittest.TestCase):
    def test_rejected_answer_is_not_submitted(self):
        outcome = Outcome("synthetic")
        TaskPipeline._apply(outcome, Verdict(False, "unaccepted", "invalid", BAD_FORMAT), "stage1")
        self.assertEqual(outcome.answer, "")

    def test_rejection_clears_previous_candidate(self):
        outcome = Outcome("synthetic", answer="previous")
        TaskPipeline._apply(outcome, Verdict(False, "", "invalid", BAD_FORMAT), "stage2")
        self.assertEqual(outcome.answer, "")
