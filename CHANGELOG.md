# Changelog

All notable changes to DeltaGauge are recorded here. The version in
[`VERSION`](./VERSION) is the single source for the release tag.

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
