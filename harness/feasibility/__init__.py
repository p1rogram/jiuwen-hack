"""Распознавание невыполнимых задач до вызова модели."""
from harness.feasibility.detector import FeasibilityDetector, blocking
from harness.feasibility.model import HARD, INFO, SOFT, Signal

__all__ = ["FeasibilityDetector", "blocking", "Signal", "HARD", "SOFT", "INFO"]
