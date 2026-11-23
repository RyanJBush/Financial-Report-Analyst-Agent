# Independent completion review — November 13, 2026

ChatGPT/Codex reproduced the completed Claude implementation from GitHub. This review
did not change the production implementation, expected facts or frozen 40-case set.

## Observed results

| Check | Result |
|---|---|
| Fresh offline checkout, Python 3.12.14, pinned dependencies | 547 development tests + 5 regression tests passed; 1 source-file test skipped |
| Same tests with the previously received, checksum-verified HTML supplied locally | 548 development + 5 regression tests passed; no skips |
| Real HTML extraction and evaluation | 6 facts, 0 issues, 0 conflicts, 7 exclusions; 6/6 numeric match; machine acceptance |
| Offline three-question demo | Exit 0; lookup, change and unsupported forecast behaved as documented |
| Frozen answer cases, snapshot mode | 40/40 cases passed |
| Frozen answer cases, current cached-source mode | 40/40 cases passed |
| Separate evaluation dimensions, both modes | Value 16/16; calculation 8/8; citation validity 40/40; appropriate non-answers 24/24; scripted narrative 16/16 |
| Expected-fact and answer-case hashes | Both matched their frozen records |
| Recorded live-model outputs | Two records inspected; their four factual claims revalidated with zero violations and manually checked against the cited evidence/calculation |
| Hosted CI on restored release | [Run 43499833414](https://github.com/RyanJBush/Financial-Report-Analyst-Agent/actions/runs/43499833414): both Python 3.11 and 3.13 jobs succeeded |

The real-source revenue change was USD 33,207 million and 15.67%, supported by
`REV_2023` and `REV_2024`, with verification level `cached_source_verified`.
The received HTML SHA-256 remains
`b0f255f67b2c26da21c69e05021ea333f4d42bafd415b153e8835a3ced2967fa`.

Commands reproduced:

```bash
python -m pip install -r requirements.lock.txt
python -m pip install --no-deps -e .
python -m pytest tests/dev tests/heldout -q
finreport demo
finreport eval-answers --out answer_eval_report.json
sha256sum -c evaluation/expected_facts.sha256
sha256sum -c evaluation/answer_cases.sha256
```

With the earlier received packet available privately, `finreport --root <packet>/run`
was used for `extract`, `evaluate`, `ask` and `eval-answers --cases-root .`. That
packet and its acquisition limitations are described in [PROVENANCE.md](PROVENANCE.md).
It is not committed here. Snapshot-mode tests remain reproducible without it.

## Publication integration

Claude published its completed implementation while Codex was independently building
from the older uploaded baseline. A later Codex publication mixed incompatible API
versions and caused a hosted test failure. Commit
`1ccc9d717e67b4c99430a6cfcc943aa8fa51cafc` restored the coherent Claude release tree
with a new commit, preserving history. Both restored CI jobs passed. The alternate
attempt is retained on `archive/codex-integration-attempt`; it is not the supported
release. Subsequent completion-review edits are documentation only.

## Limits that remain

- The live provider calls were recorded by Claude, not executed again by Codex.
  Their stored outputs and support were inspected; their billing/model execution
  provenance was not independently observed. The direct Anthropic API adapter
  remains mocked-test-only.
- Independent execution of the same exposed 40 cases does not make them a clean
  held-out benchmark. No cross-company accuracy claim is supported.
- Mutation results were not independently rerun; the earlier restriction on that
  workflow was respected. They remain implementation-author reports.
- The amendment search and original external download provenance remain unresolved.
  A limited web search did not establish an exhaustive 10-K/A result.
- Total actual effort across earlier sessions was not recorded. See [EFFORT.md](EFFORT.md).

The delivered product is a public repository with a reproducible local CLI, evidence,
tests, CI, model adapters and a demo. No Python web service or GitHub Pages app is
deployed. Further companies, PDF support and independent held-out evaluation are
future work.
