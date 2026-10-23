"""Inspect (inspect_ai) eval logs -> canonical run set.

Inspect writes one zipped ``.eval`` log per eval: a ``header.json`` member
holding the ``EvalLog`` header (``version``/``status``/``eval``/``plan``/
``results``/``stats``) and one ``samples/<id>_epoch_<n>.json`` member per
``EvalSample``. A plain ``.json`` log -- the same document with the samples
inline -- is read too, because that is what an explicit ``inspect log convert``
produces.

Each sample maps onto the canonical schema:

``model``
    ``eval.model`` from the log header -- the model under test.
``item``
    ``sample.id`` (Inspect allows an int or a str).
``judge``
    The key in ``sample.scores``: Inspect scores one sample with a dict of
    scorers, and each scorer is a judge. A log with a single scorer loses the
    dimension entirely.
``run``
    ``sample.epoch``, which is 1 for a default single-epoch task.
``score``
    ``scores[<judge>].value``.

A directory is accepted as well as a single log: a multi-model comparison is
one log per model, and merging them is what makes ``compare`` answerable. Logs
whose samples were all scored in one epoch, or by a single scorer, end in an
actionable error rather than a fabricated design.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from deltagauge.model import MIN_JUDGES, MIN_RUNS, Observation, RunSet

LOG_SUFFIX = ".eval"
HEADER_MEMBER = "header.json"
SAMPLES_PREFIX = "samples/"


def load(path: Path) -> RunSet:
    """Read one Inspect ``.eval`` log, or a directory of them, into a run set."""
    logs = _log_paths(path)
    observations: list[Observation] = []
    for log in logs:
        observations.extend(_observations(log))
    _require_gauge_design(observations, path)
    return RunSet(observations=tuple(observations))


def _log_paths(path: Path) -> list[Path]:
    """The Inspect logs named by ``path``: the file itself, or every log in a directory."""
    if path.is_dir():
        logs = sorted(path.glob(f"*{LOG_SUFFIX}"))
        if not logs:
            raise ValueError(f"{path}: no {LOG_SUFFIX} logs in this directory")
        return logs
    if not path.is_file():
        raise FileNotFoundError(f"run data file not found: {path}")
    return [path]


def _observations(log_path: Path) -> list[Observation]:
    """One canonical observation per scorer reading of every sample in one log."""
    spec, samples = _read_log(log_path)
    model = _model_of(spec, log_path)
    if not samples:
        raise ValueError(
            f"{log_path.name}: no samples; expected the samples a completed Inspect eval logs"
        )

    observations: list[Observation] = []
    for index, sample in enumerate(samples):
        where = f"{log_path.name}: sample {index}"
        item = _item_of(sample, where)
        run = _run_of(sample.get("epoch", 1), where)

        scores = sample.get("scores")
        if not isinstance(scores, dict) or not scores:
            raise ValueError(f"{where} has no scores; expected the per-scorer scores Inspect logs")
        for judge, score in scores.items():
            observations.append(
                Observation(
                    model=model,
                    item=item,
                    judge=str(judge),
                    run=run,
                    score=_score_of(score, judge, where),
                    source="inspect",
                )
            )
    return observations


def _read_log(log_path: Path) -> tuple[dict[str, object], list[dict[str, object]]]:
    """The eval spec and samples of one log, from the ``.eval`` zip or a converted JSON log."""
    if zipfile.is_zipfile(log_path):
        try:
            with zipfile.ZipFile(log_path) as archive:
                spec = _json_member(archive, HEADER_MEMBER, log_path)
                names = sorted(
                    name
                    for name in archive.namelist()
                    if name.startswith(SAMPLES_PREFIX) and name.endswith(".json")
                )
                samples = [_json_member(archive, name, log_path) for name in names]
        except zipfile.BadZipFile as error:
            raise ValueError(f"{log_path.name}: not a readable .eval archive") from error
        return spec, samples

    document = _json_object(
        log_path.read_bytes(), f"{log_path.name} (neither a zipped .eval log nor a JSON log)"
    )
    raw_samples = document.get("samples")
    samples = (
        [sample for sample in raw_samples if isinstance(sample, dict)]
        if isinstance(raw_samples, list)
        else []
    )
    return document, samples


def _json_member(archive: zipfile.ZipFile, name: str, log_path: Path) -> dict[str, object]:
    try:
        payload = archive.read(name)
    except KeyError as error:
        raise ValueError(
            f"{log_path.name}: no {name} member; is this an Inspect .eval log?"
        ) from error
    return _json_object(payload, f"{log_path.name}: {name}")


def _json_object(payload: bytes, description: str) -> dict[str, object]:
    try:
        document = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError(f"{description} is not valid JSON") from error
    if not isinstance(document, dict):
        raise ValueError(f"{description} is not a JSON object")
    return document


def _model_of(spec: dict[str, object], log_path: Path) -> str:
    eval_spec = spec.get("eval")
    model = eval_spec.get("model") if isinstance(eval_spec, dict) else None
    if isinstance(model, str) and model.strip():
        return model.strip()
    raise ValueError(f"{log_path.name}: no eval.model in the log header; is this an Inspect log?")


def _item_of(sample: dict[str, object], where: str) -> str:
    item = sample.get("id")
    if isinstance(item, (str, int)) and not isinstance(item, bool) and str(item).strip():
        return str(item).strip()
    raise ValueError(f"{where} has no id; expected the EvalSample.id Inspect logs")


def _run_of(epoch: object, where: str) -> int:
    """The 1-based epoch Inspect scored the sample in, which is 1 by default."""
    if isinstance(epoch, int) and not isinstance(epoch, bool) and epoch >= 1:
        return epoch
    raise ValueError(f"{where} has epoch {epoch!r}; expected the 1-based repeat index Inspect logs")


def _score_of(score: object, judge: object, where: str) -> float:
    """The numeric value of one scorer's score; booleans are 1.0/0.0, strings are refused."""
    value = score.get("value") if isinstance(score, dict) else None
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    raise ValueError(
        f"{where}: scorer '{judge}' returned {value!r}, which is not a number; "
        "DeltaGauge decomposes numeric scores only"
    )


def _require_gauge_design(observations: list[Observation], path: Path) -> None:
    """Name the Inspect-side fix when the logs cannot carry a gauge R&R."""
    label = path.name if path.is_file() else str(path)
    runs = {observation.run for observation in observations}
    if len(runs) < MIN_RUNS:
        raise ValueError(
            f"{label}: every sample was scored in a single epoch, so there is no run dimension to "
            f"decompose; re-run the task with `inspect eval <task> --epochs <n>` (n >= {MIN_RUNS})"
        )

    judges = {observation.judge for observation in observations}
    if len(judges) < MIN_JUDGES:
        raise ValueError(
            f"{label}: only one scorer ({', '.join(sorted(judges))}) recorded scores, so "
            f"reproducibility cannot be separated; a gauge R&R needs at least {MIN_JUDGES} scorers "
            "-- have the task score every sample with more than one judge"
        )
