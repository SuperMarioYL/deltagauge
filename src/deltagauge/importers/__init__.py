"""Source adapters that normalize an eval tool's own output into a canonical run set.

Each module in this package owns one export format -- how to find the model, the
item, the judge, the run and the score in *that* tool's files, and which
remedy to name when the format cannot carry the repetition a gauge R&R needs.
The canonical schema and its design validation stay in
:mod:`deltagauge.model`.
"""
