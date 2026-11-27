"""The error budget as a pasteable Markdown artifact.

``deltagauge report --out report.md`` writes this: the design that was measured,
the gauge R&R decomposition behind the decision limit, and a REAL / NOISE
verdict for every model pair in the run set. It is the same result the terminal
prints, in the shape a procurement or model-selection writeup can quote.
"""

from __future__ import annotations

from deltagauge.gaugerr import Delta, ErrorBudget, compare_all, error_budget
from deltagauge.model import RunSet


def format_score(value: float) -> str:
    """Render a score or a score delta: one decimal on a point scale, three on a unit scale."""
    return f"{value:.1f}" if abs(value) >= 1 else f"{value:.3f}"


UNDEFINED_SHARE = "—"
"""What a percentage share reads as when the total it divides by is zero."""


def format_share(share: float, total: float) -> str:
    """A percentage share of ``total``; an em dash when ``total`` is zero.

    A design whose measured variation is exactly zero (every cell scored the same)
    has no total to take a share of, so "0.00%" or "100.00%" would both be false.
    """
    return f"{share:.2f}%" if total else UNDEFINED_SHARE


def format_cell(value: str) -> str:
    """A table cell of user data: the name's own ``|`` must not split the row."""
    return value.replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def render_report(run_set: RunSet, *, data_source: str) -> str:
    """Render the error budget of ``run_set`` as Markdown."""
    budget = error_budget(run_set)
    comparisons = compare_all(run_set)
    sources = ", ".join(sorted({observation.source for observation in run_set.observations}))
    total_variance = budget.total_variation**2

    lines = [
        "# DeltaGauge error budget",
        "",
        f"Measurement error budget for the eval runs in `{data_source}`.",
        "",
        "## Design",
        "",
        "| models | items | judges | runs | observations |",
        "| --- | --- | --- | --- | --- |",
        "| {} | {} | {} | {} | {:,} |".format(
            len(run_set.design.models),
            len(run_set.design.items),
            len(run_set.design.judges),
            len(run_set.design.runs),
            run_set.design.observations,
        ),
        "",
        "Score range {} - {}, fully crossed and balanced; rows came from `{}`.".format(
            f"{run_set.design.score_min:g}", f"{run_set.design.score_max:g}", sources
        ),
        "",
        "## Error budget",
        "",
        "Standard deviations (SD) and variances are in score points; % is each "
        "component's share of the total score variance.",
        "",
        "| variance component | SD | variance | % of total |",
        "| --- | --- | --- | --- |",
    ]
    lines.extend(
        f"| {component.label} | {component.sd:.3f} | {component.variance:.3f} "
        f"| {format_share(component.share, total_variance)} |"
        for component in budget.components()
    )
    lines.extend(
        [
            f"| **Total GRR** | **{budget.total_grr:.3f}** "
            f"| **{budget.total_grr**2:.3f}** "
            f"| **{format_share(budget.pct_contribution, total_variance)}** |",
            f"| **Total variation** | **{budget.total_variation:.3f}** "
            f"| **{total_variance:.3f}** "
            f"| **{format_share(100.0, total_variance)}** |",
            "",
            f"- %GRR by contribution: {format_share(budget.pct_contribution, total_variance)}",
            "- %GRR by study variation: "
            f"{format_share(budget.pct_study_variation, budget.total_variation)}",
            f"- Decision limit: ±{format_score(budget.decision_limit)}",
            "",
            "## Verdicts",
            "",
            "A delta is REAL when it exceeds the decision limit, NOISE otherwise.",
            "",
        ]
    )
    if comparisons:
        lines.extend(
            [
                "| model A | model B | mean A | mean B | delta | decision limit | verdict |",
                "| --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        lines.extend(_verdict_row(comparison) for comparison in comparisons)
    else:
        lines.append(
            "This run set holds a single model, so there is no delta to judge -- "
            "the error budget above is the measurement system's own error."
        )

    lines.extend(["", "## Method", "", *_method(budget), ""])
    return "\n".join(lines)


def _verdict_row(comparison: Delta) -> str:
    delta = (
        f"+{format_score(comparison.delta)}"
        if comparison.delta > 0
        else format_score(comparison.delta)
    )
    return (
        f"| {format_cell(comparison.model_a)} | {format_cell(comparison.model_b)} "
        f"| {format_score(comparison.score_a)} | {format_score(comparison.score_b)} "
        f"| {delta} | ±{format_score(comparison.decision_limit)} "
        f"| **{comparison.verdict}** |"
    )


def _method(budget: ErrorBudget) -> list[str]:
    """The estimator's assumptions, stated so a reader can challenge the number."""
    return [
        "Gauge R&R is a crossed random-effects ANOVA (AIAG MSA) over the balanced "
        "design: each `(model, item)` pair is a part, each judge an operator and each "
        "run a replicate. Total GRR is repeatability plus reproducibility plus the "
        "item x judge interaction; part-to-part variation is signal, so it stays out of "
        "GRR and only enters the %GRR denominators. Variance components that estimate "
        "below zero are clamped to zero.",
        "",
        f"The decision limit is the gauge's own 95% measurement limit "
        f"(`z * total GRR` = ±{format_score(budget.decision_limit)}): a score delta below "
        "it cannot be told apart from the measurement system, and a larger one is "
        "reported as REAL. It is the instrument's resolution, not the standard error of "
        "a mean over items. Bootstrap and Bayesian estimators are out of scope for v0.1.",
    ]
