# Mutation results (re-run against the current tree)

Control (unmutated): dev `548 passed in 3.60s`; regression `5 passed in 0.19s`.

Detected: **75/75**. Every run imported the mutated scratch copy: True.

| # | mutation | detected | dev failures | regression failures |
|---|---|---|---|---|
| 1 | extract-all-years | yes | 3 | 1 |
| 2 | position-not-header | yes | 73 | 5 |
| 3 | ignore-negative | yes | 7 | 3 |
| 4 | ignore-scale | yes | 167 | 5 |
| 5 | conflict-never-detected | yes | 2 | 1 |
| 6 | segment-not-excluded | yes | 2 | 1 |
| 7 | nongaap-not-excluded | yes | 1 | 0 |
| 8 | accept-quarterly | yes | 2 | 0 |
| 9 | dash-as-zero | yes | 2 | 0 |
| 10 | no-403-stop | yes | 2 | 0 |
| 11 | no-error-page-check | yes | 2 | 0 |
| 12 | ua-not-required | yes | 5 | 0 |
| 13 | xbrl-scale-unchecked | yes | 2 | 0 |
| 14 | scope-evidence-skipped | yes | 2 | 0 |
| 15 | no-content-type-check | yes | 1 | 0 |
| 16 | no-redirect-check | yes | 1 | 0 |
| 17 | unbounded-retry-ok | yes | 1 | 0 |
| 18 | r1-label-cell-disqualifies-header | yes | 9 | 0 |
| 19 | r1-duplicate-columns-unchecked | yes | 1 | 0 |
| 20 | r1-ambiguous-header-unchecked | yes | 1 | 0 |
| 21 | r2-hidden-text-kept-in-cells | yes | 3 | 0 |
| 22 | r2-hidden-cell-contributes-text | yes | 2 | 0 |
| 23 | r2-white-text-not-flagged | yes | 1 | 0 |
| 24 | r2-hidden-text-outside-tables-kept | yes | 3 | 0 |
| 25 | r3-source-identity-unchecked | yes | 1 | 0 |
| 26 | r3-evidence-id-unchecked | yes | 1 | 0 |
| 27 | r3-manifest-checksum-unchecked | yes | 1 | 0 |
| 28 | r4-unresolved-conflicts-ignored | yes | 1 | 0 |
| 29 | r4-all-issues-non-blocking | yes | 2 | 0 |
| 30 | r5-local-currency-contradiction-ignored | yes | 6 | 0 |
| 31 | r5-document-currency-contradiction-ignored | yes | 5 | 0 |
| 32 | r6-non-consolidated-not-excluded | yes | 10 | 0 |
| 33 | r7-redirects-followed | yes | 1 | 0 |
| 34 | r7-redirect-status-not-refused | yes | 5 | 0 |
| 35 | r8-invalid-ix-scale-unchecked | yes | 14 | 0 |
| 36 | r8-invalid-ix-sign-unchecked | yes | 1 | 0 |
| 37 | c1-source-check-optional | yes | 11 | 0 |
| 38 | c1-verification-failure-ignored | yes | 5 | 0 |
| 39 | c1-table-vs-verified-file-unchecked | yes | 1 | 0 |
| 40 | c1-sha256-check-skipped | yes | 6 | 0 |
| 41 | c1-byte-count-check-skipped | yes | 2 | 0 |
| 42 | c1-filename-check-skipped | yes | 1 | 0 |
| 43 | c1-missing-file-not-reported | yes | 2 | 0 |
| 44 | c1-extract-skips-verification | yes | 4 | 0 |
| 45 | c2-dollar-sign-is-evidence | yes | 33 | 0 |
| 46 | c2-header-commentary-filter-off | yes | 1 | 0 |
| 47 | c2-document-commentary-filter-off | yes | 3 | 0 |
| 48 | c2-any-usd-sentence-governs | yes | 4 | 0 |
| 49 | c2-unit-not-the-amounts-own | yes | 4 | 0 |
| 50 | c2-unit-outside-resources-accepted | yes | 1 | 0 |
| 51 | c2-non-simple-unit-accepted | yes | 1 | 0 |
| 52 | c2-namespace-unchecked | yes | 1 | 0 |
| 53 | c2-non-usd-unit-not-a-contradiction | yes | 3 | 0 |
| 54 | c2-unit-details-not-required | yes | 1 | 0 |
| 55 | ans-percent-zero-guard-removed | yes | 11 | 0 |
| 56 | ans-percent-negative-guard-removed | yes | 3 | 0 |
| 57 | ans-rounding-half-even | yes | 2 | 0 |
| 58 | ans-percent-direction-swapped | yes | 18 | 0 |
| 59 | ans-percent-precision-reduced | yes | 3 | 0 |
| 60 | ans-absolute-change-direction-swapped | yes | 71 | 0 |
| 61 | ans-comparable-metric-unchecked | yes | 1 | 0 |
| 62 | ans-comparable-basis-unchecked | yes | 2 | 0 |
| 63 | ans-comparable-source-unchecked | yes | 2 | 0 |
| 64 | ans-comparable-same-period-unchecked | yes | 8 | 0 |
| 65 | ans-comparable-period-length-unchecked | yes | 1 | 0 |
| 66 | ans-normalization-consistency-unchecked | yes | 3 | 0 |
| 67 | ans-conflict-flag-ignored | yes | 1 | 0 |
| 68 | ans-blocking-issue-ignored | yes | 1 | 0 |
| 69 | ans-unverified-source-answers-anyway | yes | 10 | 0 |
| 70 | ans-future-period-check-removed | yes | 3 | 0 |
| 71 | ans-nonanswer-shape-unchecked | yes | 1 | 0 |
| 72 | ans-evidence-fields-unchecked | yes | 9 | 0 |
| 73 | ans-requested-evidence-unchecked | yes | 1 | 0 |
| 74 | ans-self-validation-skipped | yes | 1 | 0 |
| 75 | ans-any-entity-accepted | yes | 5 | 0 |

History demo (original survivor): exit 0 -> SURVIVED; 533 passed, 1 deselected in 2.87s
