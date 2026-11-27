# Changelog

All notable changes to DeltaGauge are recorded here. The version in
[`VERSION`](./VERSION) is the single source for the release tag.

## 0.2.0

Second release. Same one question, with the shipped surfaces no longer able to
misreport what they measured.

### m1 — model names are shown, not parsed

- `compare` now escapes the model names it prints in its table, so a name
  carrying Rich markup (e.g. `x[/y]`) no longer aborts the command with a
  `MarkupError`, and a complete tag is no longer rendered as a colour instead of
  as a name.

### m2 — the Markdown report survives its own delimiters

- Every model name in `report`'s verdict table is escaped for that table's `|`
  delimiter, so one name can no longer shift the verdict into a neighbouring
  column of the pasteable artifact.

### m3 — an Inspect log directory is read whole

- `ingest --from inspect <dir>` now merges the plain `.json` logs an explicit
  `inspect log convert` writes alongside the zipped `.eval` ones, instead of
  silently answering from the `.eval` logs only and refusing a directory that
  holds just the converted ones.

### m4 — a zero-variance budget no longer claims 100%

- When the measured total variation is exactly zero, the budget table and the
  report print an em dash for the shares instead of a `100.00%` share of a zero
  total (and the `0.00% of the study variation` that contradicted it).

### m5 — the version surfaces are tied together

- One contract test asserts `VERSION`, both README badges, `web/site.json`'s
  `implementation_version` and the top CHANGELOG heading name the same release.

## 0.1.0

First release. The product is one question answered with a number: is this score
gap bigger than my own eval setup's noise?

### m1 — canonical observation model

- `Observation` (`model, item, judge, run, score, source`) and `RunSet` as the
  canonical design, with pydantic validating every row.
- Balance check that rejects any run data a gauge R&R cannot decompose and names
  the missing cells, duplicated cells, or the dimension that is too small.
- Bundled demo dataset of 1,854 balanced observations; `deltagauge describe`
  prints the design.

### m2 — the error budget

- AIAG crossed random-effects ANOVA gauge R&R: repeatability, reproducibility,
  item x judge interaction and part-to-part, plus both %GRR forms.
- Decision limit as the gauge's own 95% measurement limit (`z * total GRR`).
- `deltagauge gauge` prints the budget; `deltagauge compare A B` prints the
  delta, the limit and a REAL / NOISE verdict.

### m3 — importers, report and demo

- promptfoo and Inspect importers that normalize a real export into the
  canonical run set, and refuse to fabricate a run or judge dimension.
- `deltagauge report --out report.md` writes the budget and the verdicts as a
  pasteable Markdown artifact.
- Bilingual README pair (zh primary, `README.en.md`), a recorded terminal demo
  (`docs/demo.gif`, reproducible from `docs/demo.tape`), and ruff + pytest in CI.
