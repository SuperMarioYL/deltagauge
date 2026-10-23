"""promptfoo eval exports -> canonical run set.

Reads the JSON that ``promptfoo eval -o out.json`` writes (an ``OutputFile`` or
``ResultsFile``: a ``results.results`` array of ``EvaluateResult`` rows plus the
``config`` they ran with) and maps each row onto the canonical schema:

``model``
    ``provider.id`` -- the model under test, a nested object in the export.
``item``
    The test case's ``description``, else ``test-<testIdx>``.
``judge``
    The grading provider. promptfoo records no judge column, so this is derived:
    a per-assertion ``provider`` from ``gradingResult.componentResults`` when
    every component names one (one judge reading per component), else the eval's
    grading provider from ``testCase.options.provider`` /
    ``config.defaultTest.options.provider``.
``run``
    ``repeatIndex + 1`` when the eval ran with ``repeat > 1``, else the stable
    ``repeatGroupId`` mapped to its 1-based position, else ``1``.
``score``
    The component's score for a per-component judge reading, else the row's
    ``score``.

Two promptfoo-side limitations end in an actionable error rather than a
fabricated design: an export with no repeated runs has no run dimension, and an
export graded by a single provider has no reproducibility to separate.
"""

from __future__ import annotations

import json
from pathlib import Path

from deltagauge.model import MIN_JUDGES, MIN_RUNS, Observation, RunSet


def load(path: Path) -> RunSet:
    """Read a promptfoo JSON eval export into a validated canonical run set."""
    results, config = _read_export(path)
    observations = _observations(results, config, path)
    _require_gauge_design(observations, path)
    return RunSet(observations=tuple(observations))


def _read_export(path: Path) -> tuple[list[object], dict[str, object]]:
    """The result rows and eval config of a promptfoo export."""
    if not path.is_file():
        raise FileNotFoundError(f"run data file not found: {path}")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError(
            f"{path.name}: not a promptfoo JSON export; expected the file "
            "`promptfoo eval -o out.json` writes"
        ) from error
    if not isinstance(document, dict):
        raise ValueError(f"{path.name}: not a promptfoo JSON export (expected a JSON object)")

    summary = document.get("results")
    if isinstance(summary, dict):
        results = summary.get("results")
    elif isinstance(summary, list):
        results = summary
    else:
        results = None
    if not isinstance(results, list):
        raise ValueError(
            f"{path.name}: no results array; expected the file `promptfoo eval -o out.json` writes"
        )
    config = document.get("config")
    return results, config if isinstance(config, dict) else {}


def _observations(
    results: list[object], config: dict[str, object], path: Path
) -> list[Observation]:
    """One canonical observation per judge reading of every result row."""
    group_runs: dict[tuple[str, str, str], int] = {}
    observations: list[Observation] = []
    for index, result in enumerate(results):
        where = f"{path.name}: results[{index}]"
        if not isinstance(result, dict):
            raise ValueError(f"{where} is not an object; expected a promptfoo eval result")

        model = _provider_id(result.get("provider"))
        if model is None:
            raise ValueError(f"{where} has no provider id; promptfoo writes provider.id per result")
        item = _item_id(result, where)
        run = _run_index(result, model, item, group_runs)

        readings = _component_readings(result.get("gradingResult"))
        if not readings:
            judge = _eval_judge(result, config)
            score = result.get("score")
            if judge is None:
                raise ValueError(
                    f"{where} names no grading provider, so the judge dimension cannot be derived; "
                    "set `defaultTest: {options: {provider: <judge>}}` in the promptfoo config, or a "
                    "`provider:` on the grading assertion"
                )
            if not _is_number(score):
                raise ValueError(f"{where} has no numeric score ({score!r})")
            readings = [(judge, float(score))]

        observations.extend(
            Observation(
                model=model,
                item=item,
                judge=judge,
                run=run,
                score=score,
                source="promptfoo",
            )
            for judge, score in readings
        )

    if not observations:
        raise ValueError(
            f"{path.name}: no scored results; expected the file `promptfoo eval -o out.json` writes"
        )
    return observations


def _provider_id(reference: object) -> str | None:
    """A provider reference is a plain id string or an object with ``id``/``label``."""
    if isinstance(reference, str):
        return reference.strip() or None
    if isinstance(reference, dict):
        for key in ("id", "label"):
            candidate = reference.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
    return None


def _item_id(result: dict[str, object], where: str) -> str:
    """The test case's description, else its promptfoo test index."""
    description = result.get("description")
    test_case = result.get("testCase")
    if not isinstance(description, str) or not description.strip():
        description = test_case.get("description") if isinstance(test_case, dict) else None
    if isinstance(description, str) and description.strip():
        return description.strip()

    test_idx = result.get("testIdx")
    if isinstance(test_idx, int) and not isinstance(test_idx, bool):
        return f"test-{test_idx}"
    raise ValueError(f"{where} has no test identity (description or testIdx)")


def _run_index(
    result: dict[str, object],
    model: str,
    item: str,
    repeats: dict[tuple[str, str, str], int],
) -> int:
    """The 1-based repeat index.

    promptfoo writes ``repeatIndex`` whenever it writes ``repeatGroupId``, and
    the group is shared by every provider of an expanded test case. When only
    the group is present (the export type allows it), the run index falls back
    to the occurrence number of this cell inside its group, which is the only
    repeat ordering the export then carries.
    """
    repeat_index = result.get("repeatIndex")
    if isinstance(repeat_index, int) and not isinstance(repeat_index, bool):
        return repeat_index + 1

    group = result.get("repeatGroupId")
    if isinstance(group, str) and group.strip():
        key = (group.strip(), model, item)
        repeats[key] = repeats.get(key, 0) + 1
        return repeats[key]
    return 1


def _component_readings(grading: object) -> list[tuple[str, float]]:
    """One ``(judge, score)`` per grading component when every component names its provider.

    Returns nothing when the components cannot be attributed to a judge, which
    sends the row through the eval-level grading provider instead.
    """
    if not isinstance(grading, dict):
        return []
    components = grading.get("componentResults")
    if not isinstance(components, list) or not components:
        return []

    readings: list[tuple[str, float]] = []
    for component in components:
        if not isinstance(component, dict):
            return []
        assertion = component.get("assertion")
        judge = _provider_id(assertion.get("provider")) if isinstance(assertion, dict) else None
        score = component.get("score")
        if judge is None or not _is_number(score):
            return []
        readings.append((judge, float(score)))
    return readings


def _eval_judge(result: dict[str, object], config: dict[str, object]) -> str | None:
    """The eval's grading provider, from the test case or the eval config."""
    test_case = result.get("testCase")
    if isinstance(test_case, dict):
        options = test_case.get("options")
        if isinstance(options, dict):
            judge = _provider_id(options.get("provider"))
            if judge is not None:
                return judge

    default_test = config.get("defaultTest")
    if isinstance(default_test, dict):
        options = default_test.get("options")
        if isinstance(options, dict):
            return _provider_id(options.get("provider"))
    return None


def _require_gauge_design(observations: list[Observation], path: Path) -> None:
    """Name the promptfoo-side fix when the export cannot carry a gauge R&R."""
    runs = {observation.run for observation in observations}
    if len(runs) < MIN_RUNS:
        raise ValueError(
            f"{path.name}: every result is a single execution, so there is no run dimension to "
            f"decompose; re-run the eval with `promptfoo eval --repeat <n>` (n >= {MIN_RUNS})"
        )

    judges = {observation.judge for observation in observations}
    if len(judges) < MIN_JUDGES:
        raise ValueError(
            f"{path.name}: only one grading provider ({', '.join(sorted(judges))}) scored this "
            f"export, so reproducibility cannot be separated; a gauge R&R needs at least "
            f"{MIN_JUDGES} judges -- name a second grading `provider:` on an assertion"
        )


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
