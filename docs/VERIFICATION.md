# What was verified, how, and what was not

**Independent follow-up:** [Codex completion review](INDEPENDENT_REVIEW.md) reproduced the offline tests and 40 cases, reran extraction on the received HTML, and confirmed hosted CI. The build-session record below is retained with its original scope.

Everything in this file was observed by running the stated command in this project's build session (2026-11-13). "Mine" means
produced by the AI assistant that wrote the code; it has not been independently reproduced.

## Hosted CI (GitHub Actions, observed)

| Commit | Run | Result |
|---|---|---|
| `dcde194929a62dde8b6bc2a5d951979ec6f4af5d` (code and tests identical to every later commit; later commits change documentation only) | https://github.com/RyanJBush/financial-report-analyst-agent/actions/runs/43498103522 | **success**, both matrix jobs (Python 3.11 and 3.13): install, dev tests, regression fixtures, offline demo, frozen answer cases, hash checks |

The status of the latest commit is shown on the repository's Actions tab. CI uses no network access to SEC and no model access.

## Clean-clone reproduction (observed)

Fresh `git clone` of the local repository, fresh virtual environments (`uv venv`), `pip install -e ".[dev]"`:

| Python | `pytest tests/dev` | `pytest tests/heldout` | `finreport demo` | `finreport eval-answers` | hash checks |
|---|---|---|---|---|---|
| 3.13.18 | 547 passed, 1 skipped | 5 passed | exit 0 | 40/40 cases, dimensions 16/16, 8/8, 40/40, 24/24, 16/16 | both OK |
| 3.11.18 | 547 passed, 1 skipped | 5 passed | exit 0 | same | both OK |

The one skip is `test_the_three_questions_end_to_end_on_the_received_real_filing`, which re-verifies the real filing bytes (it needs the received packet under `review/real_run_packet`, which is not published); those 6.8 MB are not in the repository. In the
development tree, where the file is present, it passes (548 passed) and the answers are produced in the stronger
`cached_source_verified` mode.

## Evaluation results

* Frozen answer cases: 40 cases, hash `a6b9036022d9...` equal to the freeze record. Results are in `evaluation/results/`:
  first run in snapshot mode and first run in cached-source mode (both 40/40 with every dimension fully passing) and a re-run at release.
  Dimensions and denominators: value 16/16, calculation 8/8, citation validity 40/40 (16 answered + 24 non-answers), appropriate
  non-answers 24/24, scripted-narrative validation 16/16. 0 false answers, 0 false non-answers.
  Caveat: the cases were authored by the same assistant that wrote the code, with the implementation in view. A 40/40 result is a
  regression baseline, not an accuracy estimate.
* Harness sensitivity: five tests in `tests/dev/test_cli_answers.py` patch the answer layer to return a wrong value, a wrong
  reason code, a mismatched citation, a wrong percentage and a false answer, and require the harness to fail the right cases.
* Extraction (recorded, one filing): 6/6 numeric match and machine acceptance in `examples/msft_fy2024/output/evaluation_report.json`.
  This was produced on the user-supplied filing bytes in the earlier milestone and independently re-run on Linux by the assistant;
  it is not re-run in CI because the filing is not in the repository.

## Mutation testing (mine)

* Original extraction/answer spec (75 mutations) against the final tree: **75/75 detected** (development tree run).
* Narrative, display, answer-evaluation, snapshot-mode and CLI modules (33 mutations): **first run 26/33**; the seven survivors (display
  direction, four harness scoring rules, two snapshot-mode rules) were closed by seven new tests; **re-run 33/33**. Both first-run
  and re-run records are kept (`review/mutations/finish_first_run/`, `review/mutations/finish/`).
* Re-run in the clean clone (Python 3.13, where one test is skipped): 75/75 and 33/33 again.
* A mutation score shows these tests notice these 108 single-line changes. It does not show the code is correct.

## Live model runs

Three `claude-cli` runs recorded in `docs/runs/` (two reached the model and validated on the first attempt; one question was not sent to a
model by design). See `docs/RUNS.md`. Manual review of 7 claims (4 live, 3 scripted): all supported (author-performed, not independent).

## Not verified / pending

* **Live Anthropic API run**: not performed (no API credential available to the build). The adapter is unit-tested against mocked HTTP only.
* **Amendment (10-K/A) search**: not performed (no SEC access from the build environment).
* **Re-download of the filing from SEC**: not performed; the reviewed bytes were supplied by the project owner. Equivalence with the
  SEC-served file is unproven (see `PROVENANCE.md`).
* **Independent mutation reproduction**: not done. Tests, frozen answer cases, source extraction and recorded narrative support were independently reviewed afterward; see `INDEPENDENT_REVIEW.md`.
* Python versions other than 3.11 and 3.13, and operating systems other than Linux, were not tested.
