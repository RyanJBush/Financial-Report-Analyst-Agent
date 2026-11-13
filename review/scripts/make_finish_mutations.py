#!/usr/bin/env python3
"""Writes review/mutations/mutations_finish.json: single-replacement mutations of the modules added in the finish phase
(narrative validator and run loop, adapters, display, answer evaluation, snapshot mode, CLI). Each `old` must occur exactly once."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
N, A, D, E, C = "src/finreport/narrative.py", "src/finreport/answers.py", "src/finreport/display.py", "src/finreport/answer_eval.py", "src/finreport/cli.py"
M = [
 (N, "validator-no-period-check", 'if label not in allowed_periods:', 'if False:', "A claim may name a fiscal year that is not in its cited evidence."),
 (N, "validator-no-number-support", 'if not expected_units:', 'if False:', "A number need not equal any cited amount."),
 (N, "validator-no-unit-check", 'if stated not in expected_units:', 'if False:', "Unit/scale words are not checked against the evidence."),
 (N, "validator-no-percent-check", 'elif norm != calc_percent:', 'elif False:', "A percentage may differ from the calculation."),
 (N, "validator-no-direction-check", 'elif (inc and cited_calc.direction != "increase") or (dec and cited_calc.direction != "decrease"):', 'elif False:', "Increase/decrease wording may contradict the calculation."),
 (N, "validator-no-forecast-filter", 'if FORECAST_RE.search(text):', 'if False:', "Forecast wording is accepted."),
 (N, "validator-no-causal-filter", 'if CAUSAL_RE.search(text):', 'if False:', "Causal/explanatory wording is accepted."),
 (N, "validator-no-framing-filter", 'if FRAMING_RE.search(text):', 'if False:', "Segment/adjusted/non-GAAP framing is accepted."),
 (N, "validator-ignore-invalid-reference", 'bad("invalid_or_irrelevant_evidence_reference", eid)', 'pass', "A citation to an ID outside the bundle is accepted."),
 (N, "validator-allow-uncited-claim", 'if not claim.evidence_ids and cited_calc is None:', 'if False:', "A claim with no citation is accepted."),
 (N, "validator-primary-value-optional", 'if primary.amount_text.replace(",", "") not in amounts:', 'if False:', "The looked-up value need not be stated."),
 (N, "validator-percentage-optional", 'bundle.calc.percent_text not in percents', 'False', "The percentage need not be stated."),
 (N, "validator-negative-sign-optional", 'bad("negative_amount_described_without_sign_or_word")', 'pass', "A negative amount may be described as positive."),
 (N, "parser-allows-extra-top-level-keys", 'if not isinstance(obj, dict) or set(obj) != {"claims"}:', 'if not isinstance(obj, dict):', "Extra keys beside 'claims' are tolerated."),
 (N, "parser-unbounded-claim-count", 'not 1 <= len(claims_raw) <= MAX_CLAIMS', 'not 1 <= len(claims_raw)', "The number of claims is unbounded."),
 (N, "run-sends-unresolved-to-model", 'if answer.status != "answered":', 'if False:', "Unresolved answers reach the model."),
 (N, "run-attempts-unbounded-by-one", 'for attempt in range(1, max(1, max_attempts) + 1):', 'for attempt in range(1, max(1, max_attempts) + 2):', "One extra attempt beyond the bound."),
 (N, "api-error-echoes-body", 'raise ProviderUnavailable(f"Anthropic API returned HTTP {err.code}") from err', 'raise ProviderUnavailable(f"Anthropic API returned HTTP {err.code}: {err.read()!r}") from err', "The provider's error body (which could echo a credential) is exposed."),
 (N, "cli-adapter-no-timeout", 'timeout=request.timeout_seconds, cwd=scratch', 'timeout=None, cwd=scratch', "The claude CLI call is not time-bounded."),
 (N, "cli-adapter-tools-enabled", '"--tools", ""', '"--tools", "default"', "The model CLI runs with tools enabled."),
 (N, "scripted-wrong-article", "art = \"an\" if b.calc.direction == \"increase\" else \"a\"", 'art = "a"', "Scripted grammar changes (cosmetic; detects only if tested)."),
 (D, "display-direction-always-increase", 'word = "increased" if v.direction == "increase" else "decreased"', 'word = "increased"', "A decrease is shown as an increase in the concise answer."),
 (D, "display-hides-weaker-verification", '"reviewed_snapshot_fact_table_only": "WEAKER: filing bytes not present; fact table matches the recorded hash of the reviewed snapshot"', '"reviewed_snapshot_fact_table_only": "full verification"', "The weaker verification level is not disclosed."),
 (E, "eval-ignores-reason-code", 'checks["non_answer"] = ok_status and ok_code', 'checks["non_answer"] = ok_status', "Reason codes are not scored."),
 (E, "eval-ignores-excerpt-support", 'if e.raw_amount_text not in e.amount_excerpt:', 'if False:', "Cited row text need not contain the amount."),
 (E, "eval-ignores-expected-ids", 'if sorted(e.evidence_id for e in a.evidence) != sorted(expected_ids):', 'if False:', "Cited IDs need not equal the expected IDs."),
 (E, "eval-ignores-percent", 'good_pct = v.percent_change_display == case["expected_percent_display"]  # type: ignore[union-attr]', 'good_pct = True', "The percentage is not scored."),
 (A, "snapshot-skips-table-hash", 'if table.source.checksum != snapshot.get("sha256"):', 'if False:', "A fact table with the wrong hash passes snapshot mode."),
 (A, "snapshot-skips-accession", 'if table.source.accession != snapshot.get("accession"):', 'if False:', "A wrong accession passes snapshot mode."),
 (A, "snapshot-excuses-any-cache-problem", 'if snapshot and check["problems"] in (["cached_file_missing"], ["no_recorded_source"]):', 'if snapshot:', "A present-but-wrong cached file is excused by the snapshot."),
 (C, "live-rejected-exits-zero", 'if narrative.status == "rejected":\n        return 8', 'if narrative.status == "rejected":\n        return 0', "A rejected live narrative exits 0."),
 (C, "live-attempt-clamp-removed", 'args.max_attempts = max(1, min(3, args.max_attempts))', 'args.max_attempts = args.max_attempts', "The attempt bound is not enforced."),
 (C, "answer-root-ignores-explicit-root", 'if root is not None:\n        return root.resolve()', 'if False:\n        return root.resolve()', "--root is ignored."),
]
spec = []
for i, (f, name, old, new, intent) in enumerate(M, 101):
    text = (ROOT / f).read_text(encoding="utf-8")
    assert text.count(old) == 1, (name, text.count(old))
    spec.append(dict(id=i, name=name, file=f, old=old, new=new, intent=intent, original_observation="finish phase (new)", batch=5))
(ROOT / "review/mutations/mutations_finish.json").write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
print(len(spec), "mutations")
