# Held-out exposure log

- Authored after `src/finreport/` and `tests/dev/` existed, by the same author (not independent). Checksums before first run: `PRE_RUN.sha256`.
- Run 1 (before any amendment): 4 passed, 1 failed (`h3_noise_tables.html`).
  - Failure: OCF facts withheld with `missing_currency_evidence`. Cause: the fixture's cash-flow table had no `$` cell and the document has no "U.S. dollars" wording, so the extractor correctly refused. Fixture defect, not an extractor defect.
- Amendment: added `$` cells to the "Net cash from operations" row of h3 only. No change to `src/`. Checksums after: `POST_AMEND.sha256`.
- Consequence: h3 is now exposed (its content was adjusted after seeing a result). h1, h2, h4, h5 were not changed after running.
- Run 2 (after h3 `$` amendment): 4 passed, 1 failed (h3). All six h3 facts were correct; the failed assertion was my *expectation* that the `SEGMENT INFORMATION` table would be excluded with reason `scope_segment_differs_from_consolidated`, while the extractor reported `statement_not_identified` because the kind check ran before the segment-cue check.
- Code change after seeing this failure: `src/finreport/extract.py` now evaluates segment and non-GAAP cues before "statement not identified" (reporting granularity only; fact selection unchanged). Dev test `test_unidentified_table_is_excluded_not_used` tightened, and a no-cue unidentified-table test added.
- Consequence: h3 is exposed twice (fixture amendment, then extractor change). The extractor change could influence any case, so h1, h2, h4, h5 are re-run below only as a regression check, not as unexposed evidence.

## Package-time clarification (added during review packaging; the entries above are unchanged)

- Classification: all five cases (h1–h5) were run repeatedly during development, h3's fixture was edited and `extract.py` was changed after observed failures. They are **development/regression evidence, not held-out evidence**. Treat a pass as "no regression on these five author-written fixtures", nothing more.
- The directory name `tests/heldout`, the `note` in `cases.json`, and the docstring in `test_heldout.py` use the old "held-out" wording. They are left byte-for-byte unchanged so that the hashes in `PRE_RUN.sha256` and `POST_AMEND.sha256` remain meaningful.
- The original failure output was not saved to a file when it occurred. It is transcribed from the session in `review/history/original_session_failures.md`, and independently reproduced by `review/scripts/reproduce_heldout_history.py` (outputs in `review/logs/heldout_history/`). The reproduction reverses the h3 edit and verifies the result against the recorded pre-run hash `cd8ded1b…`. The pre-change extractor was never hashed, so its reconstruction rests only on the fact that it reproduces the failures originally observed.
- No replacement held-out tests were created in this session. A genuinely independent held-out set would need fixtures and expectations written and frozen by someone other than the extractor's author, ideally from filings other than the one under development.

## Second independent review (R1-R8): additional exposed development cases

- An external reviewer's synthetic probes (`review/external_review/reviewer_checks.py`) and the 109 regression tests written from them
  (`tests/dev/test_review_findings.py`) are **exposed development tests**: the fixes were written against them, so a pass shows the
  reviewed failures are closed, not that unseen cases pass. No score from them is, or should be, reported as held-out.
- `tests/heldout/` (h1-h5) was not edited in this round, apart from appending this section to this log: fixtures, `cases.json`,
  `test_heldout.py`, `PRE_RUN.sha256` and `POST_AMEND.sha256` are byte-identical to the first review package. All five still pass against the fixed extractor, as a
  regression check only. Extraction code that they exercise did change (header detection, scope cues, currency, visibility).
- No replacement held-out tests were created in this session either.

## Third review round (C1/C2): fixture edits caused by a policy change, with hashes

- **Why the five fixtures changed.** C2 now requires evidence that governs the statements' reporting currency; a dollar sign, debt
  denomination, subsidiary functional currency and market commentary do not count. None of h1-h5 carried such evidence (h2 said only
  "Amounts are presented in U.S. dollars.", which does not name the financial statements), so all five correctly produced
  `missing_currency_evidence` and zero facts under the new policy (observed: 5 failed, before editing).
- **What changed, and nothing else.** Only currency wording, one governing statement per fixture, in a different legitimate form each time:
  h1 scale line "(Dollars in millions)" -> "(U.S. dollars in millions)" (x2); h2 sentence -> "The consolidated financial statements are
  presented in U.S. dollars."; h3 added "Our reporting currency is the U.S. dollar."; h4 scale lines "(In millions)" -> "(USD in millions)" (x3);
  h5 scale lines -> "(In millions of U.S. dollars)" (x2). `cases.json` (expected values, conflicts, issues, exclusions) and
  `test_heldout.py` are byte-identical to before.
- **Hash history is preserved, not overwritten.** `PRE_RUN.sha256` (before first run) and `POST_AMEND.sha256` (after the h3 amendment) are
  unchanged. `PRE_C2.sha256` records the fixtures before this round (identical to `POST_AMEND.sha256`); `POST_C2.sha256` records them after.
- **Classification unchanged:** all five are exposed development/regression evidence. The C1/C2 probes and `tests/dev/test_c1_c2.py` are
  likewise exposed development tests. No replacement held-out tests were created.
- **Negative inputs of the reviewer's probes were frozen first.** `review/c1c2/inputs/original_input_hashes.json` holds the bytes and SHA-256 of
  the probe inputs generated by the rev2 helpers BEFORE any helper or code change. `numbers_doc()` still produces identical bytes (asserted by a
  test), and the positive control gets its governing currency sentence from the cache wrapper, not from the negative base.
