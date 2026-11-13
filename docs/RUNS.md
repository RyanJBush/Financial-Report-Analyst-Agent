# Recorded live model runs

Provider: `claude-cli` adapter = the operator's installed Claude Code CLI in non-interactive mode
(`claude -p --output-format json --max-turns 1 --tools "" --no-session-persistence --system-prompt <fixed prompt>`), prompt on stdin,
scratch working directory. Model reported by the CLI: `claude-sonnet-5-5`. No credential is read, stored or logged by this project.

| Run record | Question | Outcome | Attempts | Tokens (in / out; cache create / read) | Reported cost (USD) |
|---|---|---|---|---|---|
| `20261113T195418Z-claude-cli-validated-c838ca7c.json` | revenue FY2024 | validated | 1 | 2 / 82; 798 / 1243 | 0.0041 |
| `20261113T195423Z-claude-cli-validated-8438c0e3.json` | revenue change FY2023 to FY2024 | validated | 1 | 2 / 211; 2266 / 0 | 0.0112 |
| `20261113T195423Z-claude-cli-not_generated-04494a9b.json` | revenue FY2027 (forecast) | not generated: no model call by design | 0 | - | - |

What a record contains: the structured answer (and its SHA-256), the narrative result (status, adapter, model, attempts, validated
claims, violations, a truncated raw output, usage) and the SHA-256 of the prompt actually sent. It does not contain the prompt text,
environment variables or credentials.

What these runs show: that the model, given only the verified bundle, produced claims that passed the deterministic validator on
the first attempt, for two questions on one filing. The claims were also read by hand ([`evaluation/narrative_manual_review.json`](../evaluation/narrative_manual_review.json);
author-performed). What they do **not** show: general model reliability (n = 2 answerable runs), behaviour of any other model, or
that the Anthropic API adapter works against the live service (pending: no API credential was available). The validator's rejection
paths are exercised with scripted bad outputs in `tests/dev/test_narrative.py`; no live run has been rejected.

Re-run: `finreport live "<question>" --record docs/runs`. Model output varies between runs; a different run can be rejected, and
then no model text is displayed.
