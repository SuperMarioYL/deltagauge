"""Canonical measurement schema for DeltaGauge.

Eval data enters DeltaGauge as a *design*, not as a leaderboard row. The gauge
R&R variance decomposition behind every verdict is only defined on a balanced,
fully crossed ``model x item x judge x run`` grid, so this module owns that
canonical shape -- :class:`Observation` for a single scored cell and
:class:`RunSet` for a validated grid -- and rejects any run data a gauge R&R
cannot decompose.
"""

from __future__ import annotations

import csv
import math
from collections import Counter
from itertools import product
from pathlib import Path
from typing import Sequence

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

CANONICAL_COLUMNS: tuple[str, ...] = ("model", "item", "judge", "run", "score")
"""Columns a canonical run-data CSV must provide; additional columns are ignored."""

MIN_ITEMS = 2
MIN_JUDGES = 2
MIN_RUNS = 2

MAX_REPORTED_CELLS = 8
"""Offending cells listed per problem before the message is truncated."""

Cell = tuple[str, str, str, int]


class Observation(BaseModel):
    """One scored cell: a model's answer to one item, graded by one judge in one run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str = Field(min_length=1, description="Model under test, e.g. spark-open.")
    item: str = Field(min_length=1, description="Benchmark item id, e.g. item-007.")
    judge: str = Field(min_length=1, description="The gauge that graded the cell, e.g. judge-a.")
    run: int = Field(ge=1, description="Repeat index of the run that produced the score.")
    score: float = Field(description="Score the judge gave this cell.")
    source: str = Field(default="csv", min_length=1, description="Where the row came from.")

    @field_validator("model", "item", "judge", "source", mode="before")
    @classmethod
    def _strip_padding(cls, value: object) -> object:
        """Trim padding so 'judge-a ' and 'judge-a' name the same gauge."""
        return value.strip() if isinstance(value, str) else value

    @field_validator("score")
    @classmethod
    def _require_finite(cls, value: float) -> float:
        """NaN and infinity would poison the variance decomposition silently."""
        if not math.isfinite(value):
            raise ValueError("score must be a finite number")
        return value

    @property
    def cell(self) -> Cell:
        """The design cell this observation fills."""
        return (self.model, self.item, self.judge, self.run)


class Design(BaseModel):
    """The shape of a validated run set: the grid a gauge R&R is computed over.

    Names are sorted so a printed design is reproducible from run to run.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    models: tuple[str, ...]
    items: tuple[str, ...]
    judges: tuple[str, ...]
    runs: tuple[int, ...]
    observations: int = Field(ge=1)
    score_min: float
    score_max: float


class RunSet(BaseModel):
    """A balanced, fully crossed measurement design.

    Construction validates the design, so an instance always describes data a
    gauge R&R can decompose.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    observations: tuple[Observation, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_design(self) -> RunSet:
        _require_decomposable_design(self.observations)
        return self

    @property
    def design(self) -> Design:
        """Grid dimensions and score scale of this run set."""
        return _summarize(self.observations)

    @classmethod
    def from_csv(cls, path: str | Path) -> RunSet:
        """Load the canonical CSV (``model,item,judge,run,score``) and validate its design."""
        return cls(observations=_read_canonical_csv(Path(path)))


def _dimensions(
    observations: Sequence[Observation],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[int, ...]]:
    """Sorted unique models, items, judges and runs."""
    return (
        tuple(sorted({observation.model for observation in observations})),
        tuple(sorted({observation.item for observation in observations})),
        tuple(sorted({observation.judge for observation in observations})),
        tuple(sorted({observation.run for observation in observations})),
    )


def _summarize(observations: Sequence[Observation]) -> Design:
    models, items, judges, runs = _dimensions(observations)
    scores = [observation.score for observation in observations]
    return Design(
        models=models,
        items=items,
        judges=judges,
        runs=runs,
        observations=len(observations),
        score_min=min(scores),
        score_max=max(scores),
    )


def _require_decomposable_design(observations: Sequence[Observation]) -> None:
    """Reject run data a gauge R&R cannot use, naming the offending cells."""
    models, items, judges, runs = _dimensions(observations)

    if len(items) < MIN_ITEMS:
        raise ValueError(
            f"a gauge R&R needs at least {MIN_ITEMS} items; found {len(items)} ({', '.join(items)})"
        )
    if len(judges) < MIN_JUDGES:
        raise ValueError(
            f"reproducibility needs at least {MIN_JUDGES} judges; "
            f"found {len(judges)} ({', '.join(judges)})"
        )
    if len(runs) < MIN_RUNS:
        raise ValueError(
            f"repeatability needs at least {MIN_RUNS} runs; "
            f"found {len(runs)} ({', '.join(str(run) for run in runs)})"
        )

    expected = set(product(items, judges, runs))
    per_model: dict[str, set[tuple[str, str, int]]] = {model: set() for model in models}
    seen: Counter[Cell] = Counter()
    for observation in observations:
        per_model[observation.model].add((observation.item, observation.judge, observation.run))
        seen[observation.cell] += 1

    duplicated = sorted(cell for cell, count in seen.items() if count > 1)
    if duplicated:
        raise ValueError(
            "every cell must hold exactly one score; duplicated cells: "
            + "; ".join(_format_cells(duplicated))
        )

    missing: dict[str, list[Cell]] = {}
    for model, filled in per_model.items():
        absent = expected - filled
        if absent:
            missing[model] = sorted((model, *cell) for cell in absent)
    if missing:
        lines = [
            "the design is not fully crossed, so gauge R&R cannot separate its variance components"
        ]
        for model in sorted(missing):
            cells = missing[model]
            lines.append(
                f"  {model}: {len(cells)} of {len(expected)} item / judge / run cells missing"
            )
            lines.extend(f"    {cell}" for cell in _format_cells(cells))
        raise ValueError("\n".join(lines))


def _format_cells(cells: Sequence[Cell]) -> list[str]:
    """Render cells as ``model / item / judge / run n`` lines, truncated."""
    lines = [f"{model} / {item} / {judge} / run {run}" for model, item, judge, run in cells]
    if len(lines) > MAX_REPORTED_CELLS:
        hidden = len(lines) - MAX_REPORTED_CELLS
        del lines[MAX_REPORTED_CELLS:]
        lines.append(f"... and {hidden} more")
    return lines


def _read_canonical_csv(path: Path) -> list[Observation]:
    """Parse the canonical CSV, reporting the first unusable row by line number."""
    if not path.is_file():
        raise FileNotFoundError(f"run data file not found: {path}")

    observations: list[Observation] = []
    # utf-8-sig keeps a spreadsheet-written byte order mark from hiding the first column.
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        columns = reader.fieldnames or []
        if not columns:
            raise ValueError(
                f"{path.name}: empty file, expected a header row plus one row per scored cell"
            )
        absent = [column for column in CANONICAL_COLUMNS if column not in columns]
        if absent:
            raise ValueError(
                f"{path.name}: missing required column(s) {', '.join(absent)}; "
                f"expected {', '.join(CANONICAL_COLUMNS)}"
            )
        for line_number, row in enumerate(reader, start=2):
            values = {column: row[column] for column in CANONICAL_COLUMNS}
            try:
                observations.append(Observation(**values))
            except ValidationError as error:
                raise ValueError(
                    f"{path.name}:{line_number}: {validation_message(error)}"
                ) from error

    if not observations:
        raise ValueError(f"{path.name}: no observations, expected one row per scored cell")
    return observations


def validation_message(error: ValidationError) -> str:
    """The first complaint of a validation failure, without pydantic's docs noise."""
    first = error.errors()[0]
    field = ".".join(str(part) for part in first["loc"])
    message = str(first["msg"]).removeprefix("Value error, ")
    return f"{field}: {message}" if field else message
