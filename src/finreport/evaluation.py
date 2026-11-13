"""Score an extracted fact table against frozen expectations, and decide acceptance separately.

This is the ONLY module allowed to read evaluation/expected_facts.json. It imports the schemas but never
the extractor.

Two different things are reported and must not be conflated:

1. DIAGNOSTIC numeric correctness ("numeric_fact_correctness", k/N): a fact counts only when value+sign,
   currency+scale, period and scope all match the expectation. This says nothing about where the numbers
   came from. A fact table for the wrong company with the right numbers scores N/N here.

2. ACCEPTANCE ("acceptance.accepted"): true only if ALL of the following hold, each reported as a reason
   when it fails:
     - the table validates against the FactTable schema;
     - the filing identity (company, CIK, form, accession, fiscal period end, filing date, URL) equals the
       expected source;
     - the CURRENT cached source was verified just now (caller supplies `source_check`, produced by
       ingest.verify_cached_source): the cache file exists, and its filename, byte count and SHA-256 all match the
       manifest's recorded source. Missing or altered bytes, or no verification at all, refuse acceptance;
     - the table's source checksum equals both the manifest's recorded checksum and the verified file's checksum;
     - every expected fact is present exactly once, passes the numeric checks, and has the expected evidence
       ID, original label, accounting basis, accession, source URL, source/fact/locator checksum consistency,
       statement/row/column locator agreement, and no flagged conflict;
     - there are no unexpected extra facts;
     - there are no unresolved conflicts;
     - there are no blocking extraction issues.

   Issue policy: an issue blocks acceptance unless the extractor marked it non-blocking, which it does only
   for problems inside a statement that is corroborating-only for the metric (e.g. net income in the
   cash-flow statement) - such a problem cannot invalidate a fact that has a clean authoritative source.
   Section and printed-page agreement are heuristics and are informational only.

   Acceptance means "passed these machine checks". It is not a substitute for independent human review of
   the facts and their evidence.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from finreport.schemas import FactTable

SOURCE_FIELDS = ("company", "cik", "form", "accession", "fiscal_period_end", "filing_date", "url")


def _dec(value: Any) -> Decimal:
    return Decimal(str(value))


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _diagnose(expected: dict[str, Any], table: dict[str, Any]) -> dict[str, Any]:
    shared, periods = expected["shared"], expected["periods"]
    expected_scope = "consolidated" if "consolidated" in shared["scope"].lower() else "segment"
    facts_by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for f in table["facts"]:
        facts_by_key.setdefault((f["metric"], f["period"]["label"]), []).append(f)

    results = []
    matched_keys = set()
    for exp in expected["facts"]:
        key = (exp["metric"], exp["period"])
        matched_keys.add(key)
        candidates = facts_by_key.get(key, [])
        res: dict[str, Any] = {"evidence_id": exp["evidence_id"], "found": bool(candidates), "candidates": len(candidates)}
        if not candidates:
            res.update(passed=False, checks={}, locator_info={}, acceptance_failures=["fact_missing"])
            results.append(res)
            continue
        got = candidates[0]
        per = periods[exp["period"]]
        checks = {
            "value_and_sign": _dec(got["reported_value"]) == _dec(exp["reported_value"]),
            "normalized_value": _dec(got["normalized_value"]) == _dec(exp["normalized_value_usd"]),
            "units": got["currency"] == shared["currency"] and got["scale_multiplier"] == shared["scale_multiplier"],
            "period": got["period"]["start"] == per["start"] and got["period"]["end"] == per["end"],
            "scope": got["scope"] == expected_scope,
        }
        loc, eloc = got["locator"], exp["locator"]
        info = {
            "evidence_id_matches": got["evidence_id"] == exp["evidence_id"],
            "original_label_matches": _norm(got["original_label"]) == _norm(exp["original_label"]),
            "section": {"expected": eloc["section"], "got": loc.get("section"), "match": _norm(loc.get("section")) == _norm(eloc["section"])},
            "statement": {"expected": eloc["statement"], "got": loc["statement"], "match": _norm(loc["statement"]) == _norm(eloc["statement"])},
            "printed_page": {"expected": eloc["printed_page"], "got": loc.get("printed_page"), "match": loc.get("printed_page") == eloc["printed_page"]},
            "row": {"expected": eloc["row"], "got": loc["row"], "match": _norm(loc["row"]) == _norm(eloc["row"])},
            "column": {"expected": eloc["column"], "got": loc["column"], "match": loc["column"].strip().endswith(eloc["column"])},
            "accounting_basis": {"expected": shared["accounting_basis"], "got": got["accounting_basis"],
                                 "match": got["accounting_basis"] == shared["accounting_basis"]},
            "duplicate_extractions_for_key": len(candidates),
        }
        res.update(passed=all(checks.values()), checks=checks, locator_info=info)
        results.append(res)

    extras = [f["evidence_id"] for f in table["facts"] if (f["metric"], f["period"]["label"]) not in matched_keys]
    extras += [f["evidence_id"] for (k, fs) in facts_by_key.items() if k in matched_keys for f in fs[1:]]
    return {"results": results, "extras": extras, "facts_by_key": facts_by_key}


def _accept(expected: dict[str, Any], table: dict[str, Any], diag: dict[str, Any], manifest_sha256: str | None,
            source_check: dict[str, Any] | None) -> dict[str, Any]:
    reasons: list[dict[str, Any]] = []
    informational: dict[str, Any] = {}

    try:
        FactTable.model_validate(table)
    except ValidationError as err:
        reasons.append({"code": "schema_invalid",
                        "detail": [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in err.errors()[:5]]})

    src = table.get("source") or {}
    mismatched = {f: {"expected": str(expected["source"][f]), "got": str(src.get(f))}
                  for f in SOURCE_FIELDS if str(src.get(f)) != str(expected["source"][f])}
    if mismatched:
        reasons.append({"code": "source_identity_mismatch", "detail": mismatched})

    table_sha = src.get("checksum")
    verified_sha = None
    if source_check is None:
        reasons.append({"code": "source_unverified",
                        "detail": "the current cached source was not verified against the manifest, so acceptance cannot be granted"})
    elif not source_check.get("verified"):
        reasons.append({"code": "source_verification_failed", "detail": source_check.get("problems", ["unknown"])})
    else:
        verified_sha = (source_check.get("actual") or {}).get("sha256")
        recorded_sha = (source_check.get("recorded") or {}).get("sha256")
        if manifest_sha256 is not None and manifest_sha256 != recorded_sha:
            reasons.append({"code": "manifest_checksum_inconsistent",
                            "detail": {"supplied_manifest_sha256": manifest_sha256, "recorded_in_source_check": recorded_sha}})
    mismatch_reported = False
    if manifest_sha256 is None:
        reasons.append({"code": "source_checksum_unverified",
                        "detail": "no source-manifest checksum was supplied, so the table's provenance cannot be tied to a cached file"})
    elif table_sha != manifest_sha256:
        reasons.append({"code": "source_checksum_mismatch", "detail": {"table": table_sha, "manifest": manifest_sha256}})
        mismatch_reported = True
    if verified_sha is not None and table_sha != verified_sha and not mismatch_reported:
        reasons.append({"code": "source_checksum_mismatch", "detail": {"table": table_sha, "verified_file": verified_sha}})

    missing = [r["evidence_id"] for r in diag["results"] if not r["found"]]
    if missing:
        reasons.append({"code": "expected_fact_missing", "detail": missing})

    failures: dict[str, list[str]] = {}
    for exp, res in zip(expected["facts"], diag["results"]):
        if not res["found"]:
            continue
        got = diag["facts_by_key"][(exp["metric"], exp["period"])][0]
        info, loc = res["locator_info"], got["locator"]
        failed = [name for name, ok in res["checks"].items() if not ok]
        checks = {
            "single_extraction": res["candidates"] == 1,
            "evidence_id": info["evidence_id_matches"],
            "original_label": info["original_label_matches"],
            "accounting_basis": info["accounting_basis"]["match"],
            "fact_accession": got.get("accession") == expected["source"]["accession"],
            "fact_source_url": got.get("source_url") == expected["source"]["url"],
            "fact_checksum_equals_table": got.get("source_checksum") == table_sha,
            "locator_checksum_equals_table": loc.get("source_checksum") == table_sha,
            "locator_statement": info["statement"]["match"],
            "locator_row": info["row"]["match"],
            "locator_column": info["column"]["match"],
            "no_flagged_conflict": got.get("conflict_status", "none") == "none",
        }
        failed += [name for name, ok in checks.items() if not ok]
        res["acceptance_failures"] = failed
        if failed:
            failures[exp["evidence_id"]] = failed
    if failures:
        reasons.append({"code": "fact_checks_failed", "detail": failures})

    if diag["extras"]:
        reasons.append({"code": "unexpected_extra_facts", "detail": diag["extras"]})

    conflicts = table.get("conflicts", [])
    unresolved = [c for c in conflicts if not c.get("resolved", False)]
    if unresolved:
        reasons.append({"code": "unresolved_conflicts",
                        "detail": [f"{c.get('kind')} {c.get('metric')} {c.get('period_label')}" for c in unresolved]})
    informational["resolved_conflicts"] = len(conflicts) - len(unresolved)

    issues = table.get("issues", [])
    blocking = [i for i in issues if i.get("blocking", True)]
    if blocking:
        reasons.append({"code": "blocking_extraction_issues",
                        "detail": [f"{i.get('code')} {i.get('metric')} {i.get('period_label')}" for i in blocking]})
    informational["non_blocking_issues"] = [f"{i.get('code')} {i.get('metric')} {i.get('period_label')}" for i in issues if not i.get("blocking", True)]

    informational["locator_heuristics_disagreeing"] = [
        {"evidence_id": r["evidence_id"], "field": f, **r["locator_info"][f]}
        for r in diag["results"] if r["found"] for f in ("section", "printed_page") if not r["locator_info"][f]["match"]]
    informational["currency_evidence_kinds"] = sorted({f.get("currency_evidence_kind", "unknown") for f in table.get("facts", [])})

    return {
        "accepted": not reasons,
        "decision": "accepted_by_machine_checks" if not reasons else "rejected",
        "reasons": reasons,
        "informational": informational,
        "note": "Acceptance means these machine checks passed; it does not replace independent review of the facts and their evidence.",
    }


def _unreadable(expected: dict[str, Any], err: Exception) -> dict[str, Any]:
    return {
        "numeric_fact_correctness": f"0/{len(expected['facts'])}",
        "numeric_fact_correctness_note": "DIAGNOSTIC ONLY; the table could not be read.",
        "complete_fact_correctness": f"0/{len(expected['facts'])}",
        "correct": 0, "expected_total": len(expected["facts"]), "unexpected_extra_facts": [],
        "unresolved_conflicts": 0, "resolved_conflicts": 0, "extraction_issues": 0, "blocking_issues": 0,
        "source_verification": None,
        "acceptance": {"accepted": False, "decision": "rejected",
                       "reasons": [{"code": "table_unreadable", "detail": f"{type(err).__name__}: {err}"}],
                       "informational": {}, "note": "The fact table is missing required structure."},
        "per_fact": [],
    }


def evaluate(expected: dict[str, Any], table: dict[str, Any], *, manifest_sha256: str | None = None,
             source_check: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        diag = _diagnose(expected, table)
        acceptance = _accept(expected, table, diag, manifest_sha256, source_check)
    except (KeyError, TypeError, AttributeError, IndexError, ValueError, InvalidOperation) as err:  # malformed input is a rejection, not a crash
        return _unreadable(expected, err)
    correct = sum(1 for r in diag["results"] if r["passed"])
    conflicts = table.get("conflicts", [])
    issues = table.get("issues", [])
    per_fact = [{k: v for k, v in r.items() if k != "candidates"} | {"candidates": r["candidates"]} for r in diag["results"]]
    return {
        "numeric_fact_correctness": f"{correct}/{len(expected['facts'])}",
        "numeric_fact_correctness_note": "DIAGNOSTIC ONLY: compares numbers, units, period and scope; it ignores provenance. See `acceptance`.",
        # DEPRECATED alias, identical to numeric_fact_correctness. Kept only so the external reviewer's unmodified
        # reviewer_checks.py (which reads this key) keeps running. It is a diagnostic count, never an acceptance result.
        "complete_fact_correctness": f"{correct}/{len(expected['facts'])}",
        "correct": correct,
        "expected_total": len(expected["facts"]),
        "unexpected_extra_facts": diag["extras"],
        "unresolved_conflicts": sum(1 for c in conflicts if not c.get("resolved", False)),
        "resolved_conflicts": sum(1 for c in conflicts if c.get("resolved", False)),
        "extraction_issues": len(issues),
        "blocking_issues": sum(1 for i in issues if i.get("blocking", True)),
        "source_verification": source_check,
        "acceptance": acceptance,
        "per_fact": per_fact,
    }


def evaluate_files(expected_path: Path, facts_path: Path, *, manifest_sha256: str | None = None,
                   source_check: dict[str, Any] | None = None) -> dict[str, Any]:
    return evaluate(json.loads(expected_path.read_text()), json.loads(facts_path.read_text()),
                    manifest_sha256=manifest_sha256, source_check=source_check)
