"""Command line interface for DeltaGauge."""

from __future__ import annotations

from pathlib import Path
from typing import NoReturn

import typer
from pydantic import ValidationError
from rich import box
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from deltagauge.model import CANONICAL_COLUMNS, Design, RunSet, validation_message

DEMO_DATASET = "spark-open-vs-qwen3.8-flash-next.csv"

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


def _demo_dataset() -> Path:
    """The demo dataset ships in ``examples/`` of the repository checkout."""
    return Path(__file__).resolve().parents[2] / "examples" / DEMO_DATASET


@app.callback()
def _root() -> None:
    """Is this score gap bigger than your eval setup's own noise?

    Declared explicitly because Typer collapses a single-command app into the
    root command; the callback keeps `deltagauge describe` a real subcommand.
    """


@app.command()
def describe(
    csv_file: Path | None = typer.Argument(
        None,
        metavar="[RUN_DATA]",
        help=f"Run data CSV with {', '.join(CANONICAL_COLUMNS)} columns. "
        "Defaults to the bundled demo dataset.",
    ),
) -> None:
    """Print the design of a run set: models, items, judges, runs and balance."""
    if csv_file is None:
        path = _demo_dataset()
        if not path.is_file():
            _fail(f"bundled demo dataset not found at {path}; pass a run data path")
    else:
        path = csv_file

    try:
        run_set = RunSet.from_csv(path)
    except OSError as error:
        _fail(str(error))
    except ValidationError as error:
        _fail(validation_message(error))
    except ValueError as error:
        _fail(str(error))

    _print_design(path, run_set.design)


def _print_design(path: Path, design: Design) -> None:
    console.print(f"[bold]{escape(str(path))}[/bold]")
    console.print(
        f"{len(design.models)} models, {len(design.items)} items, "
        f"{len(design.judges)} judges, {len(design.runs)} runs = "
        f"{design.observations:,} observations, balanced"
    )

    table = Table(box=box.SIMPLE, show_header=False, pad_edge=False)
    table.add_column(style="cyan", no_wrap=True)
    table.add_column()
    table.add_row("models", escape(", ".join(design.models)))
    table.add_row("items", escape(f"{design.items[0]} … {design.items[-1]}"))
    table.add_row("judges", escape(", ".join(design.judges)))
    table.add_row("runs", ", ".join(str(run) for run in design.runs))
    table.add_row("score", f"{design.score_min:g} … {design.score_max:g}")
    console.print(table)


def main() -> None:
    """Console script entry point."""
    app()
