#!/usr/bin/env python3
"""Writes review/mutations/mutations.json (the 17 distinct mutations) and unified-diff patches."""
import difflib, json
from pathlib import Path

PROJ = Path(__file__).resolve().parents[2]
SRC = "src/finreport/"
M = []
def add(name, file, old, new, intent, orig, batch=1):
    M.append(dict(id=len(M) + 1, name=name, file=SRC + file, old=old, new=new, intent=intent, original_observation=orig, batch=batch))

add("extract-all-years", "extract.py", "if col.end not in wanted_ends:", "if False:",
    "Stop filtering columns to the target year and the prior year (third-year column leaks into facts).",
    "caught; first failure test_extra_prior_year_column_is_ignored_and_not_reported")
add("position-not-header", "extract.py", "zip(ctx.columns, amounts)", "zip(ctx.columns, reversed(amounts))",
    "Map amounts to year columns in the wrong order (position instead of header-driven).",
    "caught; first failure test_six_facts_with_complete_evidence")
add("ignore-negative", "parsing.py", "return -value if negative else value", "return value",
    "Drop the sign of parenthesized / minus-prefixed amounts.",
    "caught; first failure test_parenthesized_negative_is_signed")
add("ignore-scale", "extract.py", "normalized_value=value * ctx.scale_multiplier", "normalized_value=value",
    "Do not apply the scale multiplier when normalizing.",
    "caught; first failure test_extract_and_evaluate_roundtrip_on_synthetic_cache")
add("conflict-never-detected", "extract.py", "if len({c.occurrence.normalized_value for c in primary}) > 1:", "if False:",
    "Never flag disagreeing authoritative duplicates as a conflict.",
    "caught; first failure test_thousands_vs_millions_between_authoritative_tables_is_an_unresolved_conflict")
add("segment-not-excluded", "extract.py", "if ctx.segment:", "if False:",
    "Treat segment-titled tables as consolidated candidates.",
    "caught; first failure test_segment_table_is_excluded_as_a_legitimate_scope_difference")
add("nongaap-not-excluded", "extract.py", "if ctx.non_gaap:", "if False:",
    "Treat non-GAAP / adjusted tables as GAAP candidates.",
    "caught; first failure test_non_gaap_table_is_excluded")
add("accept-quarterly", "extract.py", "elif nonannual:", "elif False:",
    "Accept 'Three Months Ended' headers as annual.",
    "caught; first failure test_quarterly_header_is_not_accepted_as_annual")
add("dash-as-zero", "extract.py", "if is_dash(cell.text):", "if False:",
    "Do not treat a dash as a missing amount (falls through to the amount parser).",
    "caught; first failure test_dash_amount_is_missing_not_zero")
add("no-403-stop", "ingest.py", "if candidate.status in (403, 429):", "if False:",
    "Retry/continue after HTTP 403/429 instead of stopping.",
    "caught; first failure test_access_denial_stops_immediately_without_retry[403]")
add("no-error-page-check", "ingest.py", "if signature in head:", "if False:",
    "Skip the SEC error-page signature check.",
    "SURVIVED the original 100-test suite (100 passed); caught after test_error_page_signature_alone_is_enough_to_reject was added (1 failed, 68 passed with -x)")
add("ua-not-required", "ingest.py", "user_agent = get_user_agent(env)  # raises before any transport call", 'user_agent = "x"',
    "Make a request without a declared User-Agent.",
    "first attempt used a malformed edit (collection error, not a valid detection); valid re-run caught (1 failed with -x)")
add("xbrl-scale-unchecked", "extract.py", "if ix_exp != _scale_exponent(ctx.scale_multiplier):", "if False:",
    "Ignore inline-XBRL scale attribute disagreement with the printed scale.",
    "caught; first failure test_inline_xbrl_scale_mismatch_is_rejected")
add("scope-evidence-skipped", "extract.py", "if consolidation is None:", "if False:",
    "Emit facts without document-level consolidation evidence.",
    "caught; first failure test_missing_consolidation_evidence_blocks_all_facts")
add("no-content-type-check", "ingest.py", "if content_type not in ACCEPTED_CONTENT_TYPES:", "if False:",
    "Accept non-HTML content types.",
    "caught (second batch; failing test id not captured in original output)", batch=2)
add("no-redirect-check", "ingest.py",
    "if (final.scheme, final.netloc, final.path) != (requested.scheme, requested.netloc, requested.path):", "if False:",
    "Accept a redirect away from the requested filing URL.",
    "caught (second batch; failing test id not captured in original output)", batch=2)
add("unbounded-retry-ok", "ingest.py", "for attempt in range(1, max_attempts + 1):", "for attempt in range(1, max_attempts + 5):",
    "Retry more times than the configured bound.",
    "caught (second batch; failing test id not captured in original output)", batch=2)
assert len(M) == 17 and len({m["name"] for m in M}) == 17  # original batches 1-2

# Batch 3: sensitivity of the tests added for review findings R1-R8 (see review/external_review/REVIEW.md).
R = "added with the R1-R8 fixes; no original observation"
add("r1-label-cell-disqualifies-header", "extract.py", "elif PERIOD_LABEL_RE.search(cell.text) or SCALE_RE.search(cell.text):", "elif False:",
    "R1: a period-label cell in the year row once again disqualifies the header row.", R, 3)
add("r1-duplicate-columns-unchecked", "extract.py", "if len(set(keys)) != len(keys):", "if False:",
    "R1: do not reject two columns for the same period.", R, 3)
add("r1-ambiguous-header-unchecked", "extract.py", "if other is not None and [c.text for c in other] != [c.text for c in header_cells]:", "if False:",
    "R1: ignore a second, different period header in the same table.", R, 3)
add("r2-hidden-text-kept-in-cells", "parsing.py", "            if child.name in SKIP_TAGS or is_hidden(child):\n                removed = True",
    "            if child.name in SKIP_TAGS:\n                removed = True",
    "R2: include hidden descendants' text in cell text.", R, 3)
add("r2-hidden-cell-contributes-text", "parsing.py", "if row_hidden or is_hidden(td):", "if False:",
    "R2: hidden rows/cells contribute their text.", R, 3)
add("r2-white-text-not-flagged", "extract.py", "if cell.ambiguous_visibility:", "if False:",
    "R2: trust amount cells containing white/transparent-coloured text.", R, 3)
add("r2-hidden-text-outside-tables-kept", "parsing.py", "            if child.name in SKIP_TAGS or is_hidden(child):\n                continue\n            style = _style(child)",
    "            if child.name in SKIP_TAGS:\n                continue\n            style = _style(child)",
    "R2: use hidden text outside tables (evidence sentences, headings).", R, 3)
add("r3-source-identity-unchecked", "evaluation.py", "    if mismatched:", "    if False:",
    "R3: do not compare filing identity in the acceptance decision.", R, 3)
add("r3-evidence-id-unchecked", "evaluation.py", '"evidence_id": info["evidence_id_matches"],', '"evidence_id": True,',
    "R3: do not require the expected evidence ID.", R, 3)
add("r3-manifest-checksum-unchecked", "evaluation.py", "elif table_sha != manifest_sha256:", "elif False:",
    "R3: do not compare the table checksum with the manifest checksum.", R, 3)
add("r4-unresolved-conflicts-ignored", "evaluation.py", "    if unresolved:", "    if False:",
    "R4: unresolved conflicts do not block acceptance.", R, 3)
add("r4-all-issues-non-blocking", "evaluation.py", 'blocking = [i for i in issues if i.get("blocking", True)]', "blocking = []",
    "R4: no extraction issue blocks acceptance.", R, 3)
add("r5-local-currency-contradiction-ignored", "extract.py", "if contradiction is not None:", "if False:",
    "R5: ignore non-USD currency text in the statement header.", R, 3)
add("r5-document-currency-contradiction-ignored", "extract.py", "if currency_conflict is not None:", "if False:",
    "R5: ignore a document-level non-USD reporting-currency statement.", R, 3)
add("r6-non-consolidated-not-excluded", "extract.py", "            if ctx.non_consolidated:", "            if False:",
    "R6: parent-only/standalone statements inherit consolidated scope.", R, 3)
add("r7-redirects-followed", "ingest.py", "opener = urllib.request.build_opener(_NoRedirect)", "opener = urllib.request.build_opener()",
    "R7: let urllib follow redirects again.", R, 3)
add("r7-redirect-status-not-refused", "ingest.py", "if 300 <= candidate.status < 400:", "if False:",
    "R7: do not give a 3xx response its explicit refusal.", R, 3)
add("r8-invalid-ix-scale-unchecked", "extract.py", "if isinstance(ix_exp, str):", "if False:",
    "R8: do not report an invalid inline-XBRL scale as such.", R, 3)
add("r8-invalid-ix-sign-unchecked", "extract.py", 'if cell.ix.get("sign") not in (None, "-"):', "if False:",
    "R8: accept any inline-XBRL sign attribute.", R, 3)
assert len(M) == 36 and len({m["name"] for m in M}) == 36

# Batch 4: sensitivity of the tests added for review findings C1/C2 (third review round).
R4 = "added with the C1/C2 fixes; no original observation"
add("c1-source-check-optional", "evaluation.py", "    if source_check is None:", "    if False:",
    "C1: acceptance may be granted without any source verification.", R4, 4)
add("c1-verification-failure-ignored", "evaluation.py", '    elif not source_check.get("verified"):', "    elif False:",
    "C1: a failed source verification does not block acceptance.", R4, 4)
add("c1-table-vs-verified-file-unchecked", "evaluation.py", "    if verified_sha is not None and table_sha != verified_sha and not mismatch_reported:", "    if False:",
    "C1: do not compare the fact table's checksum with the verified file's checksum.", R4, 4)
add("c1-sha256-check-skipped", "ingest.py", "    if sha != rec_sha:", "    if False:",
    "C1: do not compare the cached file's SHA-256 with the manifest.", R4, 4)
add("c1-byte-count-check-skipped", "ingest.py", "    if size != rec_size:", "    if False:",
    "C1: do not compare the cached file's byte count with the manifest.", R4, 4)
add("c1-filename-check-skipped", "ingest.py", "    if path.name != rec_name:", "    if False:",
    "C1: do not compare the cached filename with the manifest.", R4, 4)
add("c1-missing-file-not-reported", "ingest.py", "    if not path.is_file():", "    if False:",
    "C1: do not treat a missing/non-regular cache file as a problem.", R4, 4)
add("c1-extract-skips-verification", "cli.py", '    if not sv["verified"]:', "    if False:",
    "C1: `extract` proceeds on a cache that does not match the manifest.", R4, 4)
add("c2-dollar-sign-is-evidence", "extract.py", "currency = ctx.currency_evidence or governing_usd or unit.evidence",
    'currency = ctx.currency_evidence or governing_usd or unit.evidence or CurrencyEvidence("statement_header_usd", row.text(), "dollar sign")',
    "C2: treat the dollar symbol as enough currency evidence.", R4, 4)
add("c2-header-commentary-filter-off", "extract.py", "    if COMMENTARY_RE.search(text):", "    if False:",
    "C2: header/scale-line evidence may be commentary or debt text.", R4, 4)
add("c2-document-commentary-filter-off", "extract.py", "            if COMMENTARY_RE.search(sentence):", "            if False:",
    "C2: a document sentence about debt, hedging or FX may count as evidence.", R4, 4)
add("c2-any-usd-sentence-governs", "extract.py", "            if any(rx.search(sentence) for rx in GOVERNING_USD_RES):",
    "            if re.search(_USD_TOKEN, sentence, re.I):",
    "C2: any sentence mentioning USD counts as governing the statements.", R4, 4)
add("c2-unit-not-the-amounts-own", "extract.py", "    defs = [u for u in doc.units if u.unit_id == ref]", "    defs = list(doc.units)[:1]",
    "C2: use the first USD unit in the document instead of the amount's own unitRef.", R4, 4)
add("c2-unit-outside-resources-accepted", "parsing.py", '    for resources in soup.find_all("ix:resources"):', "    for resources in [soup]:",
    "C2: accept xbrli:unit elements anywhere in the document.", R4, 4)
add("c2-non-simple-unit-accepted", "extract.py", "    if unit.has_divide or len(unit.measures) != 1:", "    if False:",
    "C2: accept divide/multi-measure units.", R4, 4)
add("c2-namespace-unchecked", "extract.py", "    if next(iter(namespaces)) != ISO4217_NS:", "    if False:",
    "C2: do not check that the unit measure is in the ISO 4217 namespace.", R4, 4)
add("c2-non-usd-unit-not-a-contradiction", "extract.py", '                if unit.status == "other_currency":', "                if False:",
    "C2: an amount whose own unit is another currency is not treated as a conflict.", R4, 4)
add("c2-unit-details-not-required", "schemas.py", '            if not all(unit_fields) or self.currency_unit_measure != "iso4217:USD":', "            if False:",
    "C2: unit-based facts need not preserve the unit id, measure and locator.", R4, 4)
assert len(M) == 54 and len({m["name"] for m in M}) == 54

# Batch 5: sensitivity of the tests for the deterministic answer / calculation layer (src/finreport/answers.py).
R5 = "added with the answer layer; no original observation"
add("ans-percent-zero-guard-removed", "answers.py", "    if prior == 0:", "    if False:", "Compute a percentage even when the prior amount is zero.", R5, 5)
add("ans-percent-negative-guard-removed", "answers.py", "    if prior < 0:", "    if False:", "Compute a percentage from a negative prior amount.", R5, 5)
add("ans-rounding-half-even", "answers.py", "value.quantize(PERCENT_DISPLAY_PLACES, rounding=ROUND_HALF_UP)", "value.quantize(PERCENT_DISPLAY_PLACES, rounding=ROUND_HALF_EVEN)",
    "Display percentages with banker's rounding instead of ROUND_HALF_UP.", R5, 5)
add("ans-percent-direction-swapped", "answers.py", "        return (current - prior) / prior * 100, None, None", "        return (prior - current) / prior * 100, None, None",
    "Percentage change computed as (prior - current) / prior.", R5, 5)
add("ans-percent-precision-reduced", "answers.py", "PERCENT_PRECISION = 28", "PERCENT_PRECISION = 12", "Lose significant digits in the exact percentage.", R5, 5)
add("ans-absolute-change-direction-swapped", "answers.py", "    diff = c - p\n", "    diff = p - c\n", "Absolute change computed as prior - current.", R5, 5)
add("ans-comparable-metric-unchecked", "answers.py", "    if prior.metric != current.metric:", "    if False:", "Compare facts of different metrics.", R5, 5)
add("ans-comparable-basis-unchecked", "answers.py", "    if prior.accounting_basis != current.accounting_basis:", "    if False:", "Compare facts with different accounting bases.", R5, 5)
add("ans-comparable-source-unchecked", "answers.py", "    if (prior.accession, prior.source_checksum) != (current.accession, current.source_checksum):", "    if False:",
    "Silently combine facts from different filings.", R5, 5)
add("ans-comparable-same-period-unchecked", "answers.py", "    if prior.period.end == current.period.end:", "    if False:", "Compare a period with itself.", R5, 5)
add("ans-comparable-period-length-unchecked", "answers.py", "    if abs(days(prior) - days(current)) > PERIOD_LENGTH_TOLERANCE_DAYS:", "    if False:",
    "Compare periods of very different length.", R5, 5)
add("ans-normalization-consistency-unchecked", "answers.py", "    if f.normalized_value != f.reported_value * f.scale_multiplier:", "    if False:",
    "Trust a normalized amount that disagrees with reported value x scale.", R5, 5)
add("ans-conflict-flag-ignored", "answers.py", '    if f.conflict_status != "none":', "    if False:", "Use a fact flagged with a conflict.", R5, 5)
add("ans-blocking-issue-ignored", "answers.py",
    "    if any(i.blocking and i.metric == f.metric and i.period_label in (f.period.label, None) for i in book.table.issues):", "    if False:",
    "Use a fact that has a blocking extraction issue.", R5, 5)
add("ans-unverified-source-answers-anyway", "answers.py", "    if not book.verified:", "    if False:", "Answer from evidence whose source is not verified.", R5, 5)
add("ans-future-period-check-removed", "answers.py", "    if latest is not None and fy > latest:", "    if False:", "Treat a period after the evidence as just a missing period.", R5, 5)
add("ans-nonanswer-shape-unchecked", "answers.py", "            if self.value is not None or self.evidence or self.calculation is not None:", "            if False:",
    "Allow an unresolved or refused answer to carry a value, evidence or calculation.", R5, 5)
add("ans-evidence-fields-unchecked", "answers.py", "            if getattr(ref, name) != getattr(truth, name):", "            if False:",
    "Do not compare a cited evidence reference's fields with the fact table.", R5, 5)
add("ans-requested-evidence-unchecked", "answers.py", "    if expected is not None:", "    if False:", "Do not check that the cited metric and years are the ones asked for.", R5, 5)
add("ans-self-validation-skipped", "answers.py", "    if problems:  # defence in depth: an answer that fails its own validators is never returned as an answer", "    if False:",
    "Return an answer even if it fails its own validators.", R5, 5)
add("ans-any-entity-accepted", "answers.py", "    ent = _entity_pattern(company)", '    ent = r"(?:\\w+(?:\'s)?\\s+)?"', "Accept any company name in a question.", R5, 5)
assert len(M) == 75 and len({m["name"] for m in M}) == 75

out = PROJ / "review" / "mutations"
(out / "patches").mkdir(parents=True, exist_ok=True)
for m in M:
    text = (PROJ / m["file"]).read_text()
    assert text.count(m["old"]) == 1, f"{m['name']}: target not found exactly once"
    mutated = text.replace(m["old"], m["new"])
    diff = difflib.unified_diff(text.splitlines(True), mutated.splitlines(True), f"a/{m['file']}", f"b/{m['file']}", n=3)
    patch = out / "patches" / f"{m['id']:02d}_{m['name']}.patch"
    patch.write_text("".join(diff))
    m["patch"] = f"review/mutations/patches/{patch.name}"
(out / "mutations.json").write_text(json.dumps(M, indent=2) + "\n")
print(f"wrote {len(M)} mutations and patches")
