"""Command line interface for DeltaGauge."""

from __future__ import annotations

import csv
from enum import Enum
from pathlib import Path
from typing import NoReturn

import typer
from pydantic import ValidationError
from rich import box
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from deltagauge.gaugerr import ErrorBudget, error_budget
from deltagauge.gaugerr import compare as compare_models
from deltagauge.importers import csv as csv_runs
from deltagauge.importers import inspect as inspect_logs
from deltagauge.importers import promptfoo
from deltagauge.model import CANONICAL_COLUMNS, Design, RunSet, validation_message
from deltagauge.report import format_score, render_report

DEMO_DATASET = "spark-open-vs-qwen3.8-flash-next.csv"

RUN_DATA_HELP = f"Run data CSV with {', '.join(CANONICAL_COLUMNS)} columns. Defaults to the bundled demo dataset."


class Source(str, Enum):
    """The input formats ``ingest`` knows how to normalize."""

    csv = "csv"
    promptfoo = "promptfoo"
    inspect = "inspect"


app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Is this score gap bigger than your eval setup's own noise?",
)
console = Console()
error_console = Console(stderr=True)


def _fail(message: str) -> NoReturn:
    error_console.print(f"[red]error:[/red] {escape(message)}")
    raise typer.Exit(code=1)


def _demo_dataset() -> Path | None:
    """The bundled demo dataset, or ``None`` when this install does not carry it.

    Installed as a tool the dataset ships inside the package (the wheel's
    ``force-include`` copies it out of ``examples/``), and running from a
    checkout it is the ``examples/`` file itself.
    """
    candidates = (
        Path(__file__).resolve().parent / "data" / DEMO_DATASET,
        Path(__file__).resolve().parents[2] / "examples" / DEMO_DATASET,
    )
    return next((path for path in candidates if path.is_file()), None)


def _load_run_set(run_data: Path | None) -> tuple[str, RunSet]:
    """The canonical run set to work on, plus the label to show for its source.

    Without a path the bundled demo dataset is used, labelled by file name so
    the terminal output never names the checkout it was installed from.
    """
    if run_data is None:
        path = _demo_dataset()
        if path is None:
            _fail("the bundled demo dataset is missing; pass a run data path")
        label = path.name
    else:
        path = run_data
        label = str(path)

    try:
        return label, csv_runs.load(path)
    except ValidationError as error:
        _fail(validation_message(error))
    except (OSError, ValueError) as error:
        _fail(str(error))


@app.callback()
def _root() -> None:
    """Is this score gap bigger than your eval setup's own noise?

    Declared explicitly because Typer collapses a single-command app into the
    root command; the callback keeps `deltagauge describe` a real subcommand.
    """


@app.command()
def ingest(
    run_data: Path = typer.Argument(..., metavar="RUN_DATA", help="The export to normalize."),
    source: Source = typer.Option(Source.csv, "--from", "-f", help="Input format of RUN_DATA."),
    out: Path = typer.Option(
        Path("runs.csv"), "--out", "-o", help="Where to write the canonical run set CSV."
    ),
) -> None:
    """Normalize an export from any supported source into the canonical run set CSV."""
    run_set = _import_run_set(run_data, source)
    _write_canonical_csv(out, run_set)
    console.print(f"wrote [bold]{escape(str(out))}[/bold]")
    console.print(_design_summary(run_set.design))


@app.command()
def describe(
    run_data: Path | None = typer.Argument(None, metavar="[RUN_DATA]", help=RUN_DATA_HELP),
) -> None:
    """Print the design of a run set: models, items, judges, runs and balance."""
    label, run_set = _load_run_set(run_data)
    _print_design(label, run_set.design)


@app.command()
def gauge(
    run_data: Path | None = typer.Argument(None, metavar="[RUN_DATA]", help=RUN_DATA_HELP),
) -> None:
    """Print the error budget: repeatability, reproducibility, %GRR and the decision limit."""
    label, run_set = _load_run_set(run_data)
    _print_design(label, run_set.design)
    _print_budget(error_budget(run_set))


@app.command()
def compare(
    model_a: str = typer.Argument(..., metavar="MODEL_A", help="First model; the delta is A - B."),
    model_b: str = typer.Argument(..., metavar="MODEL_B", help="Second model."),
    run_data: Path | None = typer.Argument(None, metavar="[RUN_DATA]", help=RUN_DATA_HELP),
) -> None:
    """Judge two models' score delta against the measurement system's own error."""
    label, run_set = _load_run_set(run_data)
    try:
        delta = compare_models(run_set, model_a, model_b)
    except ValueError as error:
        _fail(str(error))

    colour = "green" if delta.verdict == "REAL" else "yellow"
    console.print(
        f"delta = {format_score(delta.delta)}    "
        f"decision limit = ±{format_score(delta.decision_limit)}    "
        f"verdict: [{colour}]{delta.verdict}[/{colour}]"
    )

    table = Table(box=box.SIMPLE, show_header=False, pad_edge=False)
    table.add_column(style="cyan", no_wrap=True)
    table.add_column(justify="right")
    table.add_row(delta.model_a, format_score(delta.score_a))
    table.add_row(delta.model_b, format_score(delta.score_b))
    console.print(table)
    console.print(
        f"[dim]{escape(label)} — `deltagauge gauge` shows the error budget behind the limit[/dim]"
    )


@app.command()
def report(
    run_data: Path | None = typer.Argument(None, metavar="[RUN_DATA]", help=RUN_DATA_HELP),
    out: Path | None = typer.Option(
        None, "--out", "-o", help="Write the Markdown report here (default: print it)."
    ),
) -> None:
    """Emit the error budget and its REAL / NOISE verdicts as a Markdown artifact."""
    label, run_set = _load_run_set(run_data)
    markdown = render_report(run_set, data_source=label)

    if out is None:
        typer.echo(markdown)
        return
    try:
        out.write_text(markdown, encoding="utf-8")
    except OSError as error:
        _fail(str(error))
    console.print(f"wrote [bold]{escape(str(out))}[/bold]")


def _import_run_set(path: Path, source: Source) -> RunSet:
    """Read one export into a validated run set, reporting failures as one line."""
    try:
        if source is Source.promptfoo:
            return promptfoo.load(path)
        if source is Source.inspect:
            return inspect_logs.load(path)
        return csv_runs.load(path)
    except ValidationError as error:
        _fail(validation_message(error))
    except (OSError, ValueError) as error:
        _fail(str(error))


def _write_canonical_csv(path: Path, run_set: RunSet) -> None:
    """Write the canonical CSV, sorted so one run set always writes the same file.

    Scores are written with ``repr`` so they round-trip exactly and a re-ingested
    file decomposes to the same error budget.
    """
    try:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(CANONICAL_COLUMNS)
            for observation in sorted(
                run_set.observations,
                key=lambda observation: (
                    observation.model,
                    observation.item,
                    observation.judge,
                    observation.run,
                ),
            ):
                writer.writerow(
                    [
                        observation.model,
                        observation.item,
                        observation.judge,
                        observation.run,
                        repr(observation.score),
                    ]
                )
    except OSError as error:
        _fail(str(error))


def _design_summary(design: Design) -> str:
    return (
        f"{_count(len(design.models), 'model')}, {_count(len(design.items), 'item')}, "
        f"{_count(len(design.judges), 'judge')}, {_count(len(design.runs), 'run')} = "
        f"{design.observations:,} observations, balanced"
    )


def _count(value: int, noun: str) -> str:
    return f"{value} {noun}" if value == 1 else f"{value} {noun}s"


def _print_design(label: str, design: Design) -> None:
    console.print(f"[bold]{escape(label)}[/bold]")
    console.print(_design_summary(design))

    table = Table(box=box.SIMPLE, show_header=False, pad_edge=False)
    table.add_column(style="cyan", no_wrap=True)
    table.add_column()
    table.add_row("models", escape(", ".join(design.models)))
    table.add_row("items", escape(f"{design.items[0]} … {design.items[-1]}"))
    table.add_row("judges", escape(", ".join(design.judges)))
    table.add_row("runs", ", ".join(str(run) for run in design.runs))
    table.add_row("score", f"{design.score_min:g} … {design.score_max:g}")
    console.print(table)


def _print_budget(budget: ErrorBudget) -> None:
    table = Table(box=box.SIMPLE, show_header=True, pad_edge=False, header_style="bold")
    table.add_column("variance component", style="cyan", no_wrap=True)
    table.add_column("SD", justify="right")
    table.add_column("variance", justify="right")
    table.add_column("% of total", justify="right")
    for component in budget.components():
        table.add_row(
            component.label,
            f"{component.sd:.3f}",
            f"{component.variance:.3f}",
            f"{component.share:.2f}%",
        )
    table.add_row(
        "Total GRR",
        f"{budget.total_grr:.3f}",
        f"{budget.total_grr**2:.3f}",
        f"{budget.pct_contribution:.2f}%",
        style="bold",
    )
    table.add_row(
        "Total variation",
        f"{budget.total_variation:.3f}",
        f"{budget.total_variation**2:.3f}",
        "100.00%",
        style="bold",
    )
    console.print(table)
    console.print(
        f"%GRR  {budget.pct_contribution:.2f}% of the variance, "
        f"{budget.pct_study_variation:.2f}% of the study variation"
    )
    console.print(f"decision limit  ±{format_score(budget.decision_limit)}")


def main() -> None:
    """Console script entry point."""
    app()


if __name__ == "__main__":
    main()
