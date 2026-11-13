# Evaluation record

## Frozen source facts

The six expected facts were independently checked before extraction tuning in the
earlier review workflow. `evaluation/expected_facts.json` remains byte-identical:
SHA-256 `dbc9461fdbefb74a149a70a5cbacd80496069606c69d5456ef0a0c895543c33c`.
A fact is correct only when value, units, annual period and consolidated scope all
match. Filing identity, evidence ID, accounting basis and source checks are also
required for machine acceptance. Numeric matching alone is diagnostic.

| Measurement | Observed result | Denominator and limit |
|---|---:|---|
| Source fact correctness | 6/6 | Three metrics × two periods in one filing |
| Independent direct HTML audit | 6/6 | Visible cells, year, scale, currency unit, context, entity, concept and page |
| Unresolved extraction issues/conflicts | 0/0 | Six extracted facts; seven excluded occurrences |
| Question answers | 12/12 | Nine supported questions plus three unresolved questions |
| Change calculations | 3/3 | Revenue, net income, operating cash flow; Python Decimal |
| Supported input-fact citations | 12/12 | Six lookup citations plus six change-input citations |
| Appropriate unresolved answers | 3/3 | Forecast, segment metric, unavailable fiscal year |
| Live-model success | 0/1 attempts | Local Ollama connection refused; no model output produced |

The 12 question cases were frozen after the answer implementation and before their
first evaluation. All passed on the first execution; no fixes were made against
their results. This is **post-implementation evaluation**, not an independent
held-out accuracy estimate. The result and fixture checksum are in
`evidence/answer_evaluation.json`.

## Tests and manual review

The final verification record lists observed test counts and environment details.
The test suite covers wrong years, thousands/millions normalization, negative
amounts, zero/negative denominators, missing and conflicting evidence, unknown IDs,
tampered calculations, changed/deleted cache files, scope/currency mismatches and
model output with extra prose or wrong values. Model transport tests are mocked.

Manual narrative support review covered all 12 scripted evaluation outputs: six
lookups, three changes and three unresolved answers. The factual sentences are
rendered from validated claim records; references and wording were checked against
the fact table and expected results. The renderer makes no causal, predictive or
investment claims. This review does not establish support for free-form model prose.

Earlier h1–h5 fixtures were exposed and amended during review. Their full exposure
log is retained under `tests/heldout/EXPOSURE_LOG.md`; the folder name is historical.
They are regression evidence only. A new independently authored, sealed extraction
set remains future work. The inherited mutation scores were implementation-author
reports; no independent mutation score is claimed in this release.

## Reproduce

```bash
python scripts/demo.py
python -m pytest tests/dev tests/heldout -q
python scripts/evaluate_answers.py
python evidence/independent_audit.py data/cache/msft-20240630.htm
```

The CLI is the interface. `--json` exposes the complete answer, calculation,
narrative record and supporting facts. A hosted web UI, PDF extraction, another
company and model-adapter comparisons are deferred. Actual cumulative effort from
the earlier conversations was not recorded; compliance with the 30-hour cap cannot
be verified, and no total-hours claim is made.
