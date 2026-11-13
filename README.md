# Financial Report Analyst Agent

[![CI](https://github.com/RyanJBush/financial-report-analyst-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/RyanJBush/financial-report-analyst-agent/actions/workflows/ci.yml)

Independent completion review: [observed results and limitations](docs/INDEPENDENT_REVIEW.md).

An evidence-linked analyst MVP for **one** SEC filing. It answers three kinds of questions about Microsoft's FY2024 Form 10-K from a
verified, source-linked fact table, shows the calculation and the supporting excerpts, says **"unresolved"** instead of guessing,
and checks any model-written narrative against the verified facts before showing it.

> **Scope, stated up front.** One filing (Microsoft Corporation 10-K, accession 0000950170-24-087843), two annual periods
> (FY2024, FY2023), three metrics (revenue, net income, operating cash flow). Every result below describes *this* filing and *these*
> cases. Nothing here is a claim of general financial-analysis accuracy.

## What it does

| Question | Result |
|---|---|
| "What was Microsoft's consolidated revenue in FY2024?" | **answered**: USD 245,122 million, cites `REV_2024` |
| "How much did consolidated revenue change from FY2023 to FY2024?" | **answered**: +USD 33,207 million (15.67%), cites `REV_2023`, `REV_2024` and a calculation record |
| "What will Microsoft's consolidated revenue be in FY2027?" | **unresolved** (`forecast_not_supported`): no value, no forecast, no citations |

Each answer is a structured object (status, question, value, evidence IDs with locators and excerpts, calculation record,
reason code, limitations, verification level). The CLI prints it as: question, status, concise answer, facts, calculation,
supporting excerpts, verification level, narrative, limitations. Full outputs: [`docs/demo_transcript.txt`](docs/demo_transcript.txt)
and [`examples/msft_fy2024/answers/`](examples/msft_fy2024/answers/).

## Quick start (offline: no network, no model, no credential)

Python 3.11+ (developed on 3.13).

```bash
git clone https://github.com/RyanJBush/financial-report-analyst-agent.git
cd financial-report-analyst-agent
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
finreport demo                  # the three questions above, deterministic, scripted narrative
python -m pytest -q             # development tests (tests/dev)
python -m pytest -q tests/heldout   # five regression fixtures (exposed; see "Evidence tiers")
finreport eval-answers          # 40 frozen answer cases, five separate dimensions
```

Pinned versions used for development: [`requirements.lock.txt`](requirements.lock.txt).

### Ask your own question

```bash
finreport answer "How much did net income change from FY2023 to FY2024?"          # scripted narrative
finreport answer "What was Microsoft's operating cash flow in FY2024?" --json     # machine-readable
```

Supported forms: "What was *metric* in FY*yyyy*?" and "How much did *metric* change from FY*yyyy* to FY*yyyy*?" for revenue, net
income and operating cash flow, about the filing's company. Anything else returns `unresolved` with a reason code.

### Live model narrative (optional; separate command)

```bash
finreport live "What was Microsoft's consolidated revenue in FY2024?"                      # default provider: claude-cli
finreport live "..." --provider anthropic-api --model <model-id>                           # needs ANTHROPIC_API_KEY in your environment
finreport live "..." --record docs/runs                                                    # also write a JSON run record
```

* `claude-cli` uses your own installed and authenticated [Claude Code](https://code.claude.com) CLI (`claude -p`, tools disabled,
  one turn). This project reads and stores no credential.
* `anthropic-api` reads `ANTHROPIC_API_KEY` from the environment at call time and sends it only as a request header.
* The model is given **only** the verified facts and calculation for the question (never the filing). Its JSON claims are accepted
  only if they pass the deterministic validator; otherwise no model text is shown. Exit codes: 7 provider unavailable, 8 rejected.

## Architecture and evidence flow

```
 10-K HTML --(fetch | import-file)--> cache + manifest (bytes, SHA-256) --extract--> fact table (source-linked facts)
    fact table --load + re-verify--> FactBook --answer_question--> Answer (answered | unresolved | refused) + calculation record
    Answer --bundle (cited facts only)--> adapter (scripted | claude-cli | anthropic-api) --JSON claims--> validator --> narrative or nothing
```

Details and design decisions: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). What was verified and how:
[`docs/VERIFICATION.md`](docs/VERIFICATION.md).

## Verified results

All numbers below were produced by commands in this repository and are reproducible from a clean clone unless marked otherwise.

| Check | Result | Evidence |
|---|---|---|
| Hosted CI (GitHub Actions) | see the Actions tab; the observed result for the release commit is recorded in [`docs/VERIFICATION.md`](docs/VERIFICATION.md) | `.github/workflows/ci.yml` |
| Development tests (offline) | 547 passed, 1 skipped on a clean clone on Python 3.11 and 3.13 (the skipped test re-verifies the real filing bytes, which are not in the repository; with the file present 548 pass) | `python -m pytest -q` |
| Regression fixtures | 5 passed | `python -m pytest -q tests/heldout` |
| Real-filing extraction (6 facts) | 6/6 numeric match + machine acceptance, recorded from a run on the user-supplied filing bytes | `examples/msft_fy2024/output/evaluation_report.json` |
| Frozen answer cases (40) | value 16/16 · calculation 8/8 · citation validity 40/40 · appropriate non-answers 24/24 (0 false answers, 0 false non-answers) · scripted-narrative validation 16/16 | [`evaluation/results/`](evaluation/results/) |
| Live model runs (claude-cli, `claude-sonnet-5-5`) | 2 validated first attempt; 1 question correctly never sent to a model | [`docs/runs/`](docs/runs/), [`docs/RUNS.md`](docs/RUNS.md) |
| Manual narrative-support review | 7/7 claims supported (4 live-model, 3 scripted), author-performed | [`evaluation/narrative_manual_review.json`](evaluation/narrative_manual_review.json) |
| Mutation testing (my own run, not independently reproduced) | 75/75 on the extraction/answer layer; 33/33 on the narrative/display/eval/snapshot/CLI modules (26/33 first run, 7 survivors closed with tests) | `review/mutations/` |

Dimensions are reported separately and are not combined into one score. Denominators differ because they count different things
(for example, calculation correctness counts only the 8 change cases that should be answered).

### Evidence tiers (do not conflate)

1. **Synthetic tests**: fictional-company fixtures and perturbed copies of the real fact table (wrong year, scale mismatch, negative or
   zero prior amounts, scope/currency mismatch, tampered values, duplicate facts, invalid references, malformed model output,
   injected instructions). They test behaviour; they say nothing about real filings.
2. **Real-filing checks**: extraction and answers on the one Microsoft filing, using the six reviewed facts.
3. **Scripted narratives**: a deterministic template (labelled "SCRIPTED TEMPLATE - not a model" in every output). Passing the
   validator shows the template is self-consistent; it is not evidence about a model.
4. **Actual model runs**: `claude-cli` runs recorded in `docs/runs/` (three runs; two reached a model). The direct Anthropic API
   adapter is implemented and unit-tested against a mocked HTTP layer only; **a live API run is pending** because no API credential
   was available to the build. Do not read the CLI runs as API verification.

### Who wrote what (exposure)

* The expected facts (`evaluation/expected_facts.json`, frozen, SHA-256 `dbc9461f…c33c`) were supplied by the project owner after
  independent checking by another reviewer; extraction code never reads them.
* The five regression fixtures and the earlier review probes are **exposed development evidence**, not held-out tests
  ([`tests/heldout/EXPOSURE_LOG.md`](tests/heldout/EXPOSURE_LOG.md)).
* The 40 answer cases ([`evaluation/answer_cases.json`](evaluation/answer_cases.json)) were written by the AI assistant that also
  implemented the answer layer, **with the implementation in view**, and frozen (hash + timestamp) before the evaluation harness
  existed. They are not independent held-out evidence. Expected numbers were computed with separate Decimal arithmetic
  ([`evaluation/make_answer_cases.py`](evaluation/make_answer_cases.py)). The first harness invocation aborted on a harness bug
  (a variant name was unhandled); the bug was fixed without touching any case and the next invocation is the recorded first run.
* The mutation-testing results and the manual narrative review are the assistant's own work and have not been independently reproduced.

## Ingest the real filing yourself (optional; needs network or a local copy)

The 6.8 MB filing is not in this repository. Without it, commands run in the **labelled weaker** mode
`reviewed_snapshot_fact_table_only`: the committed fact table is checked against the recorded SHA-256 of the reviewed snapshot
([`data` record](examples/msft_fy2024/data/reviewed_snapshot.json)), but the filing bytes are not re-hashed. Every output says so.
`finreport ask` never uses the weaker mode.

```bash
export SEC_USER_AGENT="<your name or organisation> <your contact email>"   # SEC requires a declared contact; never invented or stored
finreport init && finreport fetch && finreport extract && finreport evaluate && finreport ask "What was Microsoft's consolidated revenue in FY2024?"
```

`fetch` makes one polite request (≤1 req/s, stops on 403/429, rejects error pages, never follows redirects). If SEC is unreachable,
download the file elsewhere and use `finreport import-file <file> --method "<how you got it>" --sha256 <hash>`, which records only what
the tool observed. Compare the result with the reviewed snapshot hash in [`docs/PROVENANCE.md`](docs/PROVENANCE.md); a different hash
means different bytes and the facts must be re-extracted and re-reviewed.

## Provenance, currency and calculation rules

Summarised here; full text in [`docs/PROVENANCE.md`](docs/PROVENANCE.md).

* The reviewed snapshot was user-supplied (acquisition method/time not observed). SHA-256
  `b0f255f67b2c26da21c69e05021ea333f4d42bafd415b153e8835a3ced2967fa`, 6,861,379 bytes; the SEC index lists 6,860,911 bytes
  (468-byte difference attributed to script/noscript elements, **not proven**).
* USD only; a fact needs USD evidence that governs the statements (header/scale line, governing document sentence, or the amount's own
  inline-XBRL `iso4217:USD` unit). Dollar signs, debt denomination and FX commentary are not evidence; conflicts block a fact.
* `Decimal` arithmetic; change = current − prior; percentage = (current − prior) / prior × 100 at 28 significant digits, shown to two
  decimals (ROUND_HALF_UP), only when the prior amount is positive; otherwise the absolute change is returned with a reason code.
  Facts are compared only if metric, scope, accounting basis, currency and source match and they are distinct annual periods.

## Limitations and pending checks

* **Amendment search: pending.** No search for a later 10-K/A was possible (no SEC access in the build environment). Every answer says
  values are as presented in the selected historical filing and that amendments were not searched.
* **Live Anthropic API run: pending** (no credential available). The CLI adapter has real recorded runs; the API adapter does not.
* **One filing, one company, three metrics, two periods.** Grammar is a small set of templates; "revenue in 2024" without "FY" is
  unresolved by design. No PDF, other companies, dashboards, or model comparisons.
* **Validator limits.** Number/period/unit/sign/direction/citation checks are mechanical and strict; forecast, causal and
  segment/adjusted-framing filters are keyword lists, not language understanding, and deliberately conservative (e.g. "because" is
  always rejected). A claim can pass while being awkwardly worded, and a clearly fine sentence can be rejected.
* **Provenance of the filing bytes is the user's supply, not an observed SEC download**; the clean-clone demo does not re-verify them.
* **Self-authored evaluation.** The answer cases, mutation runs and narrative review were produced by the same assistant that wrote the
  code. Independent execution of the tests and 40 answer cases is recorded in `docs/INDEPENDENT_REVIEW.md`; the mutation results remain author-reported.
* Fiscal years are matched by same month/day anniversary; 52/53-week calendars with drifting year-ends are reported as not found.

## Demo script (about 90 seconds)

See [`docs/DEMO.md`](docs/DEMO.md). In short: run `finreport demo`; point at the fact IDs and locators under "Facts used"; show the
calculation steps for the change question; show that the forecast question returns no value and no citation; run `finreport live ...`
(or show `docs/runs/`) to show a model narrative passing validation; run `finreport eval-answers` for the five dimensions.

## Repository layout

| Path | Purpose |
|---|---|
| `src/finreport/` | ingestion, extraction, answers, narrative, display, answer evaluation, CLI |
| `tests/dev`, `tests/heldout`, `tests/real` | offline tests; five exposed regression fixtures; the real fact table fixture |
| `evaluation/` | frozen extraction expectations; frozen answer cases, freeze record, results, manual narrative review |
| `examples/msft_fy2024/` | committed fact table, excerpts, reviewed-snapshot record, the three example answers |
| `docs/` | architecture, provenance, verification, runs, effort, demo |
| `review/mutations`, `review/scripts` | mutation specs/results and the harness (`python review/scripts/run_mutations.py`) |
| `review/c1c2/inputs` | reviewer-supplied negative inputs used by tests |

## License and attribution

MIT ([`LICENSE`](LICENSE)). Built with Claude Code (Anthropic); commits carry `Co-Authored-By` trailers. The Microsoft filing is public SEC
data, not redistributed here; only extracted facts, short excerpts and hashes are included.

## Resume bullets (scoped to what is verified above)

* Built an evidence-linked financial-filing analyst in Python (pydantic, `Decimal`): extracts six source-linked facts from one Microsoft FY2024
  10-K and answers lookup, period-change and unsupported-forecast questions with cited evidence IDs, calculation records and explicit
  "unresolved" results; 547 offline tests plus 40 self-authored frozen cases pass on Python 3.11 and 3.13 (hosted-CI status: see docs/VERIFICATION.md).
* Added a validated LLM narrative layer (scripted adapter, Claude CLI adapter run live, Anthropic API adapter unit-tested) that rejects model
  text whose numbers, periods, units, direction or citations do not match the verified facts; checked with 33/33 self-run mutation tests
  and a manual review of 4 live-model claims. Scope: one filing; not a claim of general accuracy.
