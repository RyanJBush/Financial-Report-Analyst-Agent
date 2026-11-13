# Financial Report Analyst Agent

[![Python checks](https://github.com/RyanJBush/financial-report-analyst-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/RyanJBush/financial-report-analyst-agent/actions/workflows/ci.yml)

A local Python analyst for a deliberately small set of historical financial questions.
It extracts six consolidated facts from Microsoft's FY2024 10-K, calculates changes
with `Decimal`, and shows every input fact, source locator, and supporting excerpt.

The default narrative adapter is **scripted and deterministic**, not a live language
model. An optional local Ollama adapter returns constrained claim records; Python
validates those records and renders the factual sentences. Arbitrary model prose
is rejected. The local model was unavailable during verification; no live-model
quality result is claimed.

## Run the offline demo

Python 3.12 or 3.13 is recommended. From a terminal:

```bash
git clone https://github.com/RyanJBush/financial-report-analyst-agent.git
cd financial-report-analyst-agent
python -m venv .venv
```

Activate the environment with `source .venv/bin/activate` on macOS/Linux or
`.venv\Scripts\Activate.ps1` in Windows PowerShell, then run:

```bash
python -m pip install -r requirements.lock.txt
python -m pip install --no-deps -e .
python scripts/demo.py
python -m pytest tests/dev tests/heldout -q
python scripts/evaluate_answers.py
```

The demo restores the exact cached HTML from the compressed bundle, checks its
SHA-256, extracts facts, evaluates them, and runs lookup, change, and unsupported
questions. It makes **no network requests**. A changed existing cache is refused.

```bash
finreport ask "What was revenue in FY2024?"
finreport ask "How did revenue change from FY2023 to FY2024?" --json
finreport ask "What will revenue be in FY2027?"
```

The supported question forms are `What was <metric> in FY<year>?` and
`How did <metric> change from FY<prior> to FY<current>?`. Metrics are revenue,
net income, and operating cash flow. The selected filing supports FY2023 and
FY2024. Other phrasing, segment questions, forecasts, and causal questions return
an explicit unresolved result. `ask` exits 0 for an answer, 1 for unresolved
evidence/questions, and 2 for input or adapter failures.

## Verified example

| Question | Result | Evidence |
|---|---|---|
| FY2024 revenue | USD 245,122 million | `REV_2024`, income statement p. 56 |
| FY2023 to FY2024 revenue change | USD +33,207 million; 15.67% | `REV_2023`, `REV_2024`, `CALC_CHANGE` |
| FY2027 revenue | Unresolved: forecast not supported | No invented value or citation |

Calculation: `(245122000000 - 211915000000) / 211915000000 * 100`, rounded
with `ROUND_HALF_UP` to two decimal places. A zero or negative prior amount
produces the absolute change and an explicit reason instead of a percentage.

Local verification passed **361 tests** (356 development and five exposed regression
checks). See the [verification record](evidence/verification.json).

The six facts matched the frozen expectations **6/6**, including value, units,
period, and consolidated scope. The post-implementation question evaluation passed
**12/12** cases. These are bounded checks on this filing, not an estimate of
accuracy across companies. See [evaluation details](docs/EVALUATION.md),
[source provenance](docs/SOURCE.md), and the
[machine report](evidence/evaluation_report.json).

## How evidence flows

`checksum-pinned HTML → typed fact table → Python calculation → validated claim records → cited CLI answer`

- Ingestion caches HTML, records provenance, verifies current bytes, and rejects
  mismatches. Automated SEC access needs a user-supplied contact User-Agent,
  sends at most one request per second, stops on 403/429, and refuses redirects.
- Extraction selects annual columns by their headers and retains reported labels,
  signs, currency evidence, scale, periods, scope, accounting basis and locators.
  Repeated matching statements corroborate facts; unresolved conflicts block answers.
- Calculations use normalized base-currency values and consecutive comparable
  annual periods. Production answers never read the expected-answer fixtures.
- Narrative validation checks the exact supplied claims and calculation, including
  evidence IDs, numbers, periods and scope. Rendering uses a small supported grammar.

## Optional local model

With Ollama already running on `127.0.0.1:11434` and a model already installed:

```bash
finreport ask "What was revenue in FY2024?" --adapter ollama --model <exact-installed-model-name> --json
```

This sends one non-streaming request with a 45-second timeout. It does not install
or pull a model, use a paid API, or silently substitute scripted success on failure.
The adapter's mocked protocol tests passed; the actual local connection attempt
failed. A successful live run and manual review remain pending.

## Scope and limitations

One selected 10-K, as presented in accession `0000950170-24-087843`; a comprehensive
10-K/A search is pending. The supplied HTML is 468 bytes larger than the SEC index
entry. Those bytes match the lengths of script/noscript elements, but original
download provenance and byte equivalence to the indexed source remain unproven.
The bundle preserves the received bytes and never executes its scripts.

The extractor is tuned for a small statement layout family. CSS classes/external
stylesheets are not interpreted; some legitimate currency wording and 52/53-week
calendars may be withheld. The files under `tests/heldout` are **exposed regression
fixtures**, not a clean held-out accuracy result. No independent mutation score,
cross-company accuracy, PDF support, or live-model quality is claimed.

This repository hosts code and evidence for a local CLI. It does not deploy a
Python web service. See [the demo script](docs/DEMO.md) and
[verified resume bullets](docs/RESUME.md).
