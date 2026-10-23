"""Tests for the DeltaGauge measurement pipeline.

One section per build milestone: the canonical schema and its balance check
(m1), the gauge R&R error budget and the verdict it drives (m2), and the
importers, the report and the CLI that ties them together (m3). Everything runs
offline against the bundled demo data and fixtures built in-process -- no model
calls, no network and no external binaries.
"""

from __future__ import annotations

import json
import zipfile
from itertools import product
from pathlib import Path

import numpy as np
import pytest
from typer.testing import CliRunner

from deltagauge.cli import app
from deltagauge.gaugerr import (
    DECISION_QUANTILE,
    compare,
    compare_all,
    error_budget,
    model_scores,
)
from deltagauge.importers import csv as csv_runs
from deltagauge.importers import inspect as inspect_logs
from deltagauge.importers import promptfoo
from deltagauge.model import Observation, RunSet
from deltagauge.report import render_report

REPO_ROOT = Path(__file__).resolve().parents[1]
DEMO_CSV = REPO_ROOT / "examples" / "spark-open-vs-qwen3.8-flash-next.csv"

DEMO_MODEL_A = "spark-open"
DEMO_MODEL_B = "qwen3.8-flash-next"
DEMO_OBSERVATIONS = 1854
DEMO_DECISION_LIMIT = 1.4
DEMO_DELTA = -1.0


def _observation(
    model: str = "model-a",
    item: str = "item-001",
    judge: str = "judge-a",
    run: int = 1,
    score: float = 40.0,
    source: str = "csv",
) -> Observation:
    return Observation(model=model, item=item, judge=judge, run=run, score=score, source=source)


def _crossed(
    *,
    models: tuple[str, ...] = ("model-a", "model-b"),
    items: int = 2,
    judges: int = 2,
    runs: int = 2,
    score: float = 40.0,
) -> RunSet:
    """A complete, balanced design with every cell set to ``score``."""
    names = [f"item-{index:03d}" for index in range(1, items + 1)]
    gauges = [f"judge-{letter}" for letter in "abcd"[:judges]]
    observations = [
        _observation(model, item, judge, run, score)
        for model, item, judge, run in product(models, names, gauges, range(1, runs + 1))
    ]
    return RunSet(observations=tuple(observations))


def _variance_design(
    *,
    seed: int,
    parts: int = 60,
    judges: int = 3,
    runs: int = 3,
    sd_part: float = 10.0,
    sd_judge: float = 0.5,
    sd_error: float = 0.3,
    gap: float = 0.0,
) -> RunSet:
    """A random-effects design with known variance components.

    ``(model, item)`` is the part, every judge draws one effect it keeps for the
    whole study and every replicate adds independent error, so the injected
    standard deviations are exactly what the decomposition has to recover. The
    item effect is shared by both models, which keeps the model-to-model delta
    equal to ``gap`` instead of to a chance difference between two part samples.
    """
    rng = np.random.default_rng(seed)
    items = [f"item-{index:03d}" for index in range(1, parts + 1)]
    item_effect = dict(zip(items, rng.normal(0.0, sd_part, parts), strict=True))
    gauges = [f"judge-{letter}" for letter in "abcdef"[:judges]]
    judge_effect = rng.normal(0.0, sd_judge, judges)

    observations: list[Observation] = []
    for model in ("model-a", "model-b"):
        shift = -gap / 2 if model == "model-a" else gap / 2
        for item in items:
            for index, judge in enumerate(gauges):
                for run in range(1, runs + 1):
                    score = (
                        40.0
                        + shift
                        + item_effect[item]
                        + float(judge_effect[index])
                        + float(rng.normal(0.0, sd_error))
                    )
                    observations.append(_observation(model, item, judge, run, score))
    return RunSet(observations=tuple(observations))


def _promptfoo_export(models: tuple[str, ...], *, repeats: int = 3, judges: int = 2) -> dict:
    """A minimal promptfoo ``-o out.json`` export with one component per judge."""
    gauges = [f"judge-{letter}" for letter in "abc"[:judges]]
    results = []
    for model, item_index, repeat in product(models, range(1, 3), range(1, repeats + 1)):
        results.append(
            {
                "provider": {"id": model},
                "description": f"item-{item_index:03d}",
                "repeatIndex": repeat - 1,
                "gradingResult": {
                    "componentResults": [
                        {
                            "assertion": {"provider": judge},
                            "score": 39.0 + index + repeat / 10,
                        }
                        for index, judge in enumerate(gauges)
                    ]
                },
            }
        )
    return {"results": {"results": results}, "config": {}}


def _inspect_log(
    *,
    model: str = "spark-open",
    items: int = 2,
    epochs: int = 2,
    judges: int = 2,
) -> bytes:
    """An Inspect ``.eval`` archive: a header plus one sample per item and epoch."""
    gauges = [f"judge-{letter}" for letter in "abc"[:judges]]
    import io

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "header.json",
            json.dumps({"version": 2, "status": "success", "eval": {"model": model}}),
        )
        for item_index, epoch in product(range(1, items + 1), range(1, epochs + 1)):
            archive.writestr(
                f"samples/item-{item_index:03d}_{epoch}.json",
                json.dumps(
                    {
                        "id": f"item-{item_index:03d}",
                        "epoch": epoch,
                        "scores": {
                            judge: {"value": 39.0 + index + epoch / 10}
                            for index, judge in enumerate(gauges)
                        },
                    }
                ),
            )
    return buffer.getvalue()


# --- m1: canonical schema and balance validation ----------------------------


def test_demo_file_loads_as_a_balanced_design() -> None:
    design = csv_runs.load(DEMO_CSV).design

    assert design.models == (DEMO_MODEL_B, DEMO_MODEL_A) or design.models == (
        DEMO_MODEL_A,
        DEMO_MODEL_B,
    )
    assert len(design.models) == 2
    assert len(design.items) == 103
    assert len(design.judges) == 3
    assert design.runs == (1, 2, 3)
    assert design.observations == DEMO_OBSERVATIONS


def test_every_cell_holds_exactly_one_score() -> None:
    run_set = _crossed(items=2, judges=2, runs=2)

    assert len(run_set.observations) == 2 * 2 * 2 * 2


def test_missing_cells_are_named() -> None:
    complete = _crossed(items=2, judges=2, runs=2)
    holed = tuple(
        observation
        for observation in complete.observations
        if not (observation.model == "model-a" and observation.judge == "judge-b")
    )

    with pytest.raises(ValueError) as failure:
        RunSet(observations=holed)

    message = str(failure.value)
    assert "not fully crossed" in message
    assert "model-a / item-001 / judge-b / run 1" in message


def test_duplicated_cells_are_rejected() -> None:
    duplicated = _crossed(items=2, judges=2, runs=2).observations + (_observation(score=41.0),)

    with pytest.raises(ValueError, match="duplicated cells"):
        RunSet(observations=duplicated)


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"items": 1}, "at least 2 items"),
        ({"judges": 1}, "at least 2 judges"),
        ({"runs": 1}, "at least 2 runs"),
    ],
)
def test_a_dimension_too_small_to_decompose_is_rejected(kwargs: dict, expected: str) -> None:
    with pytest.raises(ValueError, match=expected):
        _crossed(**kwargs)


def test_an_empty_csv_reports_its_header_problem(tmp_path: Path) -> None:
    empty = tmp_path / "empty.csv"
    empty.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="empty file"):
        csv_runs.load(empty)


def test_a_csv_missing_a_canonical_column_names_it(tmp_path: Path) -> None:
    partial = tmp_path / "partial.csv"
    partial.write_text("model,item,judge,run\nmodel-a,item-001,judge-a,1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="missing required column\\(s\\) score"):
        csv_runs.load(partial)


def test_a_non_finite_score_is_refused_by_line_number(tmp_path: Path) -> None:
    broken = tmp_path / "broken.csv"
    broken.write_text(
        "model,item,judge,run,score\nmodel-a,item-001,judge-a,1,nan\n", encoding="utf-8"
    )

    with pytest.raises(ValueError) as failure:
        csv_runs.load(broken)

    assert "broken.csv:2" in str(failure.value)
    assert "finite" in str(failure.value)


# --- m2: the error budget, the decision limit and the verdict ---------------


def test_the_bundled_demo_reports_a_noise_verdict() -> None:
    run_set = csv_runs.load(DEMO_CSV)
    budget = error_budget(run_set)
    delta = compare(run_set, DEMO_MODEL_A, DEMO_MODEL_B)

    assert delta.delta == pytest.approx(DEMO_DELTA, abs=1e-6)
    assert budget.decision_limit == pytest.approx(DEMO_DECISION_LIMIT, abs=0.05)
    assert delta.verdict == "NOISE"


def test_repeatability_recovers_the_injected_run_to_run_error() -> None:
    budget = error_budget(_variance_design(seed=1, sd_error=0.3, sd_judge=0.0))

    assert budget.repeatability == pytest.approx(0.3, rel=0.15)


def test_judge_spread_is_attributed_to_reproducibility() -> None:
    none = error_budget(_variance_design(seed=1, sd_judge=0.0))
    spread = error_budget(_variance_design(seed=1, sd_judge=1.0))

    assert none.reproducibility < 0.1
    assert spread.reproducibility > 0.5


def test_part_to_part_is_signal_and_stays_out_of_grr() -> None:
    budget = error_budget(_variance_design(seed=1, sd_part=10.0))

    assert budget.part_to_part == pytest.approx(10.0, rel=0.2)
    assert budget.total_grr < budget.part_to_part / 10
    assert budget.pct_contribution < 5.0


def test_the_decision_limit_is_the_gauges_95_percent_measurement_limit() -> None:
    budget = error_budget(_variance_design(seed=2))

    assert budget.decision_limit == pytest.approx(DECISION_QUANTILE * budget.total_grr)


def test_the_verdict_flips_at_the_decision_limit() -> None:
    run_set = _variance_design(seed=3)
    limit = error_budget(run_set).decision_limit

    inside = compare(run_set, "model-a", "model-b")
    assert abs(inside.delta) < limit
    assert inside.verdict == "NOISE"

    pushed_set = _variance_design(seed=3, gap=4 * limit)
    pushed = compare(pushed_set, "model-a", "model-b")
    assert abs(pushed.delta) > error_budget(pushed_set).decision_limit
    assert pushed.verdict == "REAL"


def test_a_delta_is_judged_between_the_two_named_models() -> None:
    run_set = _variance_design(seed=4, gap=2.0)
    scores = model_scores(run_set)

    assert set(scores) == {"model-a", "model-b"}
    assert scores["model-b"] - scores["model-a"] == pytest.approx(2.0, abs=0.1)
    assert compare(run_set, "model-a", "model-b").delta == pytest.approx(-2.0, abs=0.1)
    assert compare(run_set, "model-b", "model-a").delta == pytest.approx(2.0, abs=0.1)
    assert compare_all(run_set)[0].verdict in {"REAL", "NOISE"}


def test_comparing_an_unknown_model_names_the_models_present() -> None:
    with pytest.raises(ValueError, match="no observations for model 'model-z'"):
        compare(_crossed(), "model-z", "model-a")


def test_a_zero_variance_design_has_a_zero_decision_limit() -> None:
    budget = error_budget(_crossed(score=40.0))

    assert budget.total_grr == 0.0
    assert budget.decision_limit == 0.0


# --- m3: importers, the report and the CLI ---------------------------------


def test_csv_ingest_round_trips_through_the_canonical_format(tmp_path: Path) -> None:
    runner = CliRunner()
    canonical = tmp_path / "runs.csv"

    result = runner.invoke(app, ["ingest", str(DEMO_CSV), "--from", "csv", "--out", str(canonical)])

    assert result.exit_code == 0, result.output
    assert "1,854 observations" in result.output
    reloaded = csv_runs.load(canonical)
    assert reloaded.design.observations == DEMO_OBSERVATIONS
    assert error_budget(reloaded).decision_limit == pytest.approx(
        error_budget(csv_runs.load(DEMO_CSV)).decision_limit
    )


def test_promptfoo_import_derives_the_judge_dimension(tmp_path: Path) -> None:
    export = tmp_path / "evals.json"
    export.write_text(json.dumps(_promptfoo_export(("spark-open",))), encoding="utf-8")

    run_set = promptfoo.load(export)
    design = run_set.design

    assert design.models == ("spark-open",)
    assert len(design.items) == 2
    assert len(design.judges) == 2
    assert design.runs == (1, 2, 3)
    assert {observation.source for observation in run_set.observations} == {"promptfoo"}


def test_promptfoo_export_without_repeats_asks_for_repeat(tmp_path: Path) -> None:
    export = tmp_path / "single.json"
    export.write_text(json.dumps(_promptfoo_export(("spark-open",), repeats=1)), encoding="utf-8")

    with pytest.raises(ValueError, match="--repeat"):
        promptfoo.load(export)


def test_promptfoo_export_graded_by_one_provider_asks_for_a_second_judge(
    tmp_path: Path,
) -> None:
    export = tmp_path / "single-judge.json"
    export.write_text(
        json.dumps(
            {
                "results": {
                    "results": [
                        {
                            "provider": {"id": "spark-open"},
                            "description": f"item-{index:03d}",
                            "repeatIndex": repeat - 1,
                            "score": 39.0,
                        }
                        for index in (1, 2)
                        for repeat in (1, 2)
                    ]
                },
                "config": {"defaultTest": {"options": {"provider": "judge-a"}}},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="at least 2 judges"):
        promptfoo.load(export)


def test_promptfoo_import_rejects_a_foreign_json_document(tmp_path: Path) -> None:
    foreign = tmp_path / "notes.json"
    foreign.write_text(json.dumps({"hello": "world"}), encoding="utf-8")

    with pytest.raises(ValueError, match="no results array"):
        promptfoo.load(foreign)


def test_inspect_import_reads_a_zipped_eval_log(tmp_path: Path) -> None:
    log = tmp_path / "spark-open.eval"
    log.write_bytes(_inspect_log())

    run_set = inspect_logs.load(log)
    design = run_set.design

    assert design.models == ("spark-open",)
    assert len(design.items) == 2
    assert len(design.judges) == 2
    assert design.runs == (1, 2)
    assert {observation.source for observation in run_set.observations} == {"inspect"}


def test_inspect_log_with_one_epoch_asks_for_epochs(tmp_path: Path) -> None:
    log = tmp_path / "single-epoch.eval"
    log.write_bytes(_inspect_log(epochs=1))

    with pytest.raises(ValueError, match="--epochs"):
        inspect_logs.load(log)


def test_inspect_log_with_one_scorer_asks_for_a_second_judge(tmp_path: Path) -> None:
    log = tmp_path / "single-scorer.eval"
    log.write_bytes(_inspect_log(judges=1))

    with pytest.raises(ValueError, match="at least 2 scorers"):
        inspect_logs.load(log)


def test_report_quotes_the_demo_verdict_and_the_method() -> None:
    markdown = render_report(csv_runs.load(DEMO_CSV), data_source=DEMO_CSV.name)

    assert "| **Total GRR** |" in markdown
    assert "Decision limit: ±1.4" in markdown
    assert "| **NOISE** |" in markdown
    assert "crossed random-effects ANOVA" in markdown
    assert "Bootstrap and Bayesian estimators are out of scope" in markdown


def test_report_says_so_when_there_is_no_delta_to_judge() -> None:
    markdown = render_report(_crossed(models=("model-a",)), data_source="one-model.csv")

    assert "single model" in markdown
    assert "## Verdicts" in markdown


def test_cli_uses_the_bundled_dataset_when_no_path_is_given() -> None:
    result = CliRunner().invoke(app, ["describe"])

    assert result.exit_code == 0, result.output
    assert "1,854 observations, balanced" in result.output


def test_cli_describe_gauge_compare_and_report_run_end_to_end(tmp_path: Path) -> None:
    runner = CliRunner()

    described = runner.invoke(app, ["describe", str(DEMO_CSV)])
    assert described.exit_code == 0, described.output
    assert "2 models, 103 items, 3 judges, 3 runs = 1,854 observations, balanced" in (
        described.output
    )

    gauged = runner.invoke(app, ["gauge", str(DEMO_CSV)])
    assert gauged.exit_code == 0, gauged.output
    assert "Repeatability" in gauged.output
    assert "decision limit" in gauged.output

    judged = runner.invoke(app, ["compare", DEMO_MODEL_A, DEMO_MODEL_B, str(DEMO_CSV)])
    assert judged.exit_code == 0, judged.output
    assert "delta = -1.0" in judged.output
    assert "decision limit = ±1.4" in judged.output
    assert "NOISE" in judged.output

    target = tmp_path / "report.md"
    reported = runner.invoke(app, ["report", str(DEMO_CSV), "--out", str(target)])
    assert reported.exit_code == 0, reported.output
    assert "DeltaGauge error budget" in target.read_text(encoding="utf-8")


def test_cli_fails_loudly_on_an_unbalanced_design(tmp_path: Path) -> None:
    broken = tmp_path / "unbalanced.csv"
    rows = ["model,item,judge,run,score"]
    for model, item, judge, run in product(
        ("model-a", "model-b"), ("item-001", "item-002"), ("judge-a", "judge-b"), (1, 2)
    ):
        if (model, item, judge, run) == ("model-a", "item-002", "judge-b", 2):
            continue
        rows.append(f"{model},{item},{judge},{run},40.0")
    broken.write_text("\n".join(rows) + "\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["describe", str(broken)])

    assert result.exit_code == 1
    assert "not fully crossed" in result.output
