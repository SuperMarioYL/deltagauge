"""AIAG-style crossed ANOVA gauge R&R over an eval run set.

DeltaGauge treats each ``(model, item)`` pair as a *part* -- the thing being
measured -- every judge as an *operator* and every repeat as a *replicate*. A
:class:`~deltagauge.model.RunSet` is validated to be exactly that fully crossed
design, so every cell the decomposition needs is present.

The decomposition splits the score variance into four independent components,
all reported as standard deviations in score points:

``repeatability``
    The same judge scoring the same part differently from run to run
    (equipment variation, EV).
``reproducibility``
    One judge being systematically harsher or more generous than another,
    averaged over parts (appraiser variation, AV).
``interaction``
    Judges disagreeing about specific items -- a judge that is not uniformly
    shifted but item-dependent.
``part_to_part``
    The real spread between the things being measured (PV). This is signal, not
    error, so it stays out of GRR and only enters the ``%GRR`` denominators.

``total_grr`` is the measurement system's own error: repeatability,
reproducibility and the interaction together. The decision limit is the
gauge's own 95% measurement limit, ``z * total_grr``: a score delta smaller
than that cannot be told apart from the measurement system, so it is reported
as NOISE, and a larger one as REAL.

The estimator is the classical random-effects ANOVA of a crossed study (AIAG
MSA), computed with :mod:`numpy`. Variance components that estimate below zero
are clamped to zero, the standard AIAG treatment of a negative mean square.
Bootstrap and Bayesian estimators are deliberately out of scope for v0.1.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import combinations
from typing import Literal, Sequence

import numpy as np
from scipy.stats import norm

from deltagauge.model import Observation, RunSet

DECISION_COVERAGE = 0.975
"""Two-sided coverage of the decision limit: a 95% measurement limit."""

DECISION_QUANTILE = float(norm.ppf(DECISION_COVERAGE))

Verdict = Literal["REAL", "NOISE"]


@dataclass(frozen=True)
class Component:
    """One variance component of the error budget, in score points."""

    label: str
    sd: float
    variance: float
    share: float
    """This component's percentage of the total score variance."""


@dataclass(frozen=True)
class ErrorBudget:
    """The measurement system's own error, as a gauge R&R error budget.

    Every field is a standard deviation in score points except the two
    percentages and the decision limit, which is also in score points.
    """

    repeatability: float
    reproducibility: float
    interaction: float
    part_to_part: float
    total_grr: float
    total_variation: float
    pct_contribution: float
    """Total GRR as a percentage of the total variance (AIAG %Contribution)."""
    pct_study_variation: float
    """Total GRR as a percentage of the total variation (AIAG %Study Var)."""
    decision_limit: float

    def components(self) -> tuple[Component, ...]:
        """The four independent variance components, in report order."""
        total_variance = self.total_variation**2
        return (
            _component("Repeatability (same judge, same item)", self.repeatability, total_variance),
            _component("Reproducibility (judge to judge)", self.reproducibility, total_variance),
            _component("Item x judge interaction", self.interaction, total_variance),
            _component("Part-to-part (model x item)", self.part_to_part, total_variance),
        )


@dataclass(frozen=True)
class Delta:
    """A model-to-model score delta judged against the measurement error."""

    model_a: str
    model_b: str
    score_a: float
    score_b: float
    delta: float
    """``score_a - score_b``."""
    decision_limit: float
    verdict: Verdict


def error_budget(run_set: RunSet) -> ErrorBudget:
    """Decompose a run set's score variance into a gauge R&R error budget."""
    grid = _crossed_grid(run_set.observations)
    parts, judges, runs = grid.shape

    grand = float(grid.mean())
    part_means = grid.mean(axis=(1, 2))
    judge_means = grid.mean(axis=(0, 2))
    cell_means = grid.mean(axis=2)

    ss_part = judges * runs * float(((part_means - grand) ** 2).sum())
    ss_judge = parts * runs * float(((judge_means - grand) ** 2).sum())
    ss_interaction = runs * float(
        ((cell_means - part_means[:, None] - judge_means[None, :] + grand) ** 2).sum()
    )
    ss_error = float(((grid - cell_means[:, :, None]) ** 2).sum())

    ms_part = ss_part / (parts - 1)
    ms_judge = ss_judge / (judges - 1)
    ms_interaction = ss_interaction / ((parts - 1) * (judges - 1))
    ms_error = ss_error / (parts * judges * (runs - 1))

    # Mean squares are combined into variance components the AIAG way, with the
    # part x judge interaction as the reference mean square for judge and part.
    var_repeatability = ms_error
    var_interaction = _clamp((ms_interaction - ms_error) / runs)
    var_reproducibility = _clamp((ms_judge - ms_interaction) / (parts * runs))
    var_part_to_part = _clamp((ms_part - ms_interaction) / (judges * runs))

    var_grr = var_repeatability + var_reproducibility + var_interaction
    var_total = var_grr + var_part_to_part
    total_grr = math.sqrt(var_grr)
    total_variation = math.sqrt(var_total)

    return ErrorBudget(
        repeatability=math.sqrt(var_repeatability),
        reproducibility=math.sqrt(var_reproducibility),
        interaction=math.sqrt(var_interaction),
        part_to_part=math.sqrt(var_part_to_part),
        total_grr=total_grr,
        total_variation=total_variation,
        pct_contribution=_percent(var_grr, var_total),
        pct_study_variation=_percent(total_grr, total_variation),
        decision_limit=DECISION_QUANTILE * total_grr,
    )


def model_scores(run_set: RunSet) -> dict[str, float]:
    """Mean score per model over every observation, keyed in model name order."""
    totals: dict[str, float] = {}
    counts: dict[str, int] = {}
    for observation in run_set.observations:
        totals[observation.model] = totals.get(observation.model, 0.0) + observation.score
        counts[observation.model] = counts.get(observation.model, 0) + 1
    return {model: totals[model] / counts[model] for model in sorted(totals)}


def compare(run_set: RunSet, model_a: str, model_b: str) -> Delta:
    """Judge one model-to-model delta against the run set's own decision limit."""
    return _delta(model_scores(run_set), error_budget(run_set), model_a, model_b)


def compare_all(run_set: RunSet) -> tuple[Delta, ...]:
    """Every unordered model pair in the run set, ordered by model name."""
    scores = model_scores(run_set)
    budget = error_budget(run_set)
    return tuple(_delta(scores, budget, a, b) for a, b in combinations(sorted(scores), 2))


def _delta(scores: dict[str, float], budget: ErrorBudget, model_a: str, model_b: str) -> Delta:
    for model in (model_a, model_b):
        if model not in scores:
            raise ValueError(
                f"no observations for model '{model}'; the run set has: {', '.join(sorted(scores))}"
            )
    delta = scores[model_a] - scores[model_b]
    return Delta(
        model_a=model_a,
        model_b=model_b,
        score_a=scores[model_a],
        score_b=scores[model_b],
        delta=delta,
        decision_limit=budget.decision_limit,
        verdict="REAL" if abs(delta) > budget.decision_limit else "NOISE",
    )


def _crossed_grid(observations: Sequence[Observation]) -> np.ndarray:
    """The scores as a ``(parts, judges, runs)`` array; a part is a (model, item) pair."""
    parts = sorted({(observation.model, observation.item) for observation in observations})
    judges = sorted({observation.judge for observation in observations})
    runs = sorted({observation.run for observation in observations})
    part_index = {part: index for index, part in enumerate(parts)}
    judge_index = {judge: index for index, judge in enumerate(judges)}
    run_index = {run: index for index, run in enumerate(runs)}

    grid = np.full((len(parts), len(judges), len(runs)), np.nan)
    for observation in observations:
        grid[
            part_index[(observation.model, observation.item)],
            judge_index[observation.judge],
            run_index[observation.run],
        ] = observation.score

    if np.isnan(grid).any():
        # A RunSet is validated to be fully crossed, so a hole here means that
        # invariant was bypassed. Fail loudly rather than decompose a hole.
        raise ValueError(
            "the run set is not fully crossed; gauge R&R needs one score per item / judge / run cell"
        )
    return grid


def _component(label: str, sd: float, total_variance: float) -> Component:
    variance = sd**2
    return Component(
        label=label,
        sd=sd,
        variance=variance,
        share=_percent(variance, total_variance),
    )


def _clamp(variance: float) -> float:
    """A negative variance component is a zero component, the AIAG convention."""
    return max(variance, 0.0)


def _percent(part: float, whole: float) -> float:
    """``part`` as a percentage of ``whole``, zero when ``whole`` is zero."""
    return 100.0 * part / whole if whole else 0.0
