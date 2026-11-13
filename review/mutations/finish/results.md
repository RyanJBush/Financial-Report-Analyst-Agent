# Mutation results (re-run against the current tree)

Control (unmutated): dev `548 passed in 3.40s`; regression `5 passed in 0.17s`.

Detected: **33/33**. Every run imported the mutated scratch copy: True.

| # | mutation | detected | dev failures | regression failures |
|---|---|---|---|---|
| 101 | validator-no-period-check | yes | 3 | 0 |
| 102 | validator-no-number-support | yes | 5 | 0 |
| 103 | validator-no-unit-check | yes | 2 | 0 |
| 104 | validator-no-percent-check | yes | 2 | 0 |
| 105 | validator-no-direction-check | yes | 2 | 0 |
| 106 | validator-no-forecast-filter | yes | 2 | 0 |
| 107 | validator-no-causal-filter | yes | 2 | 0 |
| 108 | validator-no-framing-filter | yes | 1 | 0 |
| 109 | validator-ignore-invalid-reference | yes | 3 | 0 |
| 110 | validator-allow-uncited-claim | yes | 1 | 0 |
| 111 | validator-primary-value-optional | yes | 1 | 0 |
| 112 | validator-percentage-optional | yes | 1 | 0 |
| 113 | validator-negative-sign-optional | yes | 1 | 0 |
| 114 | parser-allows-extra-top-level-keys | yes | 2 | 0 |
| 115 | parser-unbounded-claim-count | yes | 1 | 0 |
| 116 | run-sends-unresolved-to-model | yes | 10 | 0 |
| 117 | run-attempts-unbounded-by-one | yes | 3 | 0 |
| 118 | api-error-echoes-body | yes | 1 | 0 |
| 119 | cli-adapter-no-timeout | yes | 1 | 0 |
| 120 | cli-adapter-tools-enabled | yes | 1 | 0 |
| 121 | scripted-wrong-article | yes | 1 | 0 |
| 122 | display-direction-always-increase | yes | 1 | 0 |
| 123 | display-hides-weaker-verification | yes | 1 | 0 |
| 124 | eval-ignores-reason-code | yes | 1 | 0 |
| 125 | eval-ignores-excerpt-support | yes | 1 | 0 |
| 126 | eval-ignores-expected-ids | yes | 1 | 0 |
| 127 | eval-ignores-percent | yes | 1 | 0 |
| 128 | snapshot-skips-table-hash | yes | 1 | 0 |
| 129 | snapshot-skips-accession | yes | 1 | 0 |
| 130 | snapshot-excuses-any-cache-problem | yes | 1 | 0 |
| 131 | live-rejected-exits-zero | yes | 2 | 0 |
| 132 | live-attempt-clamp-removed | yes | 1 | 0 |
| 133 | answer-root-ignores-explicit-root | yes | 5 | 0 |