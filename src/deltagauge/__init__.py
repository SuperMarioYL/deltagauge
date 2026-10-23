"""DeltaGauge: a gauge R&R error budget for LLM-eval score deltas."""

from deltagauge.gaugerr import Delta, ErrorBudget, Verdict, compare, error_budget
from deltagauge.model import Design, Observation, RunSet

__all__ = [
    "Delta",
    "Design",
    "ErrorBudget",
    "Observation",
    "RunSet",
    "Verdict",
    "compare",
    "error_budget",
]
