"""Canonical CSV run data -> canonical run set.

``model,item,judge,run,score`` is DeltaGauge's own interchange format, the one
``ingest`` writes and every other importer normalizes into, so this adapter is
deliberately thin: it is the ``ingest --from csv`` entry point and nothing more.

Parsing and design validation stay in :mod:`deltagauge.model`, which owns the
canonical schema; this module only gives the format the same ``load(path)``
shape as the promptfoo and Inspect adapters, so ``ingest`` reaches every source
through one interface.
"""

from __future__ import annotations

from pathlib import Path

from deltagauge.model import RunSet


def load(path: Path) -> RunSet:
    """Read the canonical CSV into a validated run set."""
    return RunSet.from_csv(path)
