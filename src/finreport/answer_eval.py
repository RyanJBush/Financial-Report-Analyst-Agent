"""Evaluation of the answer layer against the frozen case file (the frozen `answer_cases.json`, kept in the project's evaluation directory).

Dimensions are reported SEPARATELY, each with its own raw counts and denominator, and are never merged into one score:

  1. value correctness      - an expected-answered case returns exactly the expected value (display / amount / direction / percentage).
  2. calculation correctness - change cases only: absolute change, direction, percentage (or the exact withheld reason) and a valid
                               calculation record that recomputes.
  3. citation validity       - answered cases cite exactly the expected evidence IDs, each resolving to exactly one fact whose cited
                               row text contains the amount; non-answers cite nothing (no value, no evidence, no calculation).
  4. appropriate non-answers - expected-unresolved/refused cases return an allowed status and reason code; false answers and false
                               non-answers are counted separately.
  5. narrative (scripted)    - the SCRIPTED narrative passes the deterministic validator for answered cases. This is a self-consistency
                               check of a template; it is not evidence about a model. Support of live-model text is judged by the
                               separate manual review record, not here.

The cases were written by the same assistant that wrote the answer layer, with the implementation in view, and frozen before this
harness existed (see the freeze record). Results describe THESE cases on THIS filing only.
This module only reads the case file; production code (answers.py, narrative.py) never reads it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from finreport.answers import Answer, FactBook, answer_question, validate_calculation
from finreport.narrative import ScriptedAdapter, run_narrative
from finreport.schemas import FactTable


def _cases_path(root: Path) -> Path:
    return root / "evaluation" / "answer_cases.json"


def load_cases(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    path = _cases_path(root)
    raw = path.read_bytes()
    freeze_path = path.with_name("answer_cases.freeze.json")
    freeze = json.loads(freeze_path.read_text(encoding="utf-8")) if freeze_path.exists() else {}
    info = {"cases_sha256": hashlib.sha256(raw).hexdigest(), "frozen_sha256": freeze.get("sha256"), "frozen_at_utc": freeze.get("frozen_at_utc")}
    info["matches_freeze_record"] = info["cases_sha256"] == info["frozen_sha256"]
    return json.loads(raw.decode("utf-8")), info


# ------------------------------------------------------------------------------------------------ fixture variants


def _edit(table: FactTable, evidence_id: str, **updates: Any) -> FactTable:
    return table.model_copy(update={"facts": [f.model_copy(update=updates) if f.evidence_id == evidence_id else f for f in table.facts]})


def _set_amount(table: FactTable, evidence_id: str, reported: str, scale: int) -> FactTable:
    r = Decimal(reported)
    return _edit(table, evidence_id, reported_value=r, scale_multiplier=scale, normalized_value=r * scale)


def apply_variant(variant: str | None, table: FactTable) -> FactTable:
    """Return a damaged COPY of the fact table (no revalidation, so inconsistent states can be built)."""
    if variant in (None, "source_unverified"):
        return table
    if variant == "tampered_normalized_value":
        f = next(x for x in table.facts if x.evidence_id == "REV_2024")
        return _edit(table, "REV_2024", normalized_value=f.normalized_value + 1)
    if variant == "duplicate_fact":
        return table.model_copy(update={"facts": list(table.facts) + [next(x for x in table.facts if x.evidence_id == "REV_2024")]})
    if variant == "fact_checksum_mismatch":
        return _edit(table, "REV_2024", source_checksum="0" * 64)
    if variant == "prior_zero":
        return _set_amount(table, "REV_2023", "0", 1_000_000)
    if variant == "prior_negative":
        return _set_amount(table, "REV_2023", "-50", 1_000_000)
    if variant == "prior_in_thousands":
        orig = next(x for x in table.facts if x.evidence_id == "REV_2023")
        return _set_amount(table, "REV_2023", str(orig.reported_value), 1_000)   # same printed number, wrongly scaled in thousands
    if variant == "drop_NI_2023":
        return table.model_copy(update={"facts": [f for f in table.facts if f.evidence_id != "NI_2023"]})
    if variant == "scope_segment_REV_2023":
        return _edit(table, "REV_2023", scope="segment")
    if variant == "currency_eur_REV_2023":
        return _edit(table, "REV_2023", currency="EUR")
    raise ValueError(f"unknown variant {variant}")


def rebook(base: FactBook, table: FactTable, variant: str | None, snapshot: dict[str, Any] | None) -> FactBook:
    """Rebuild a FactBook over a (possibly damaged) table the same way the loader would, at the same verification level."""
    if variant == "source_unverified":
        return FactBook.from_table(table, source_check=None)
    if base.level == "reviewed_snapshot_fact_table_only" and snapshot is not None:
        return FactBook.from_reviewed_snapshot(table, snapshot)
    stub = {"verified": True, "problems": [], "actual": {"sha256": base.table.source.checksum}}
    return FactBook.from_table(table, source_check=stub if base.verified else None)


# ------------------------------------------------------------------------------------------------ scoring


@dataclass
class CaseResult:
    id: str
    group: str
    category: str
    expected_answered: bool
    status: str
    reason_code: str | None
    checks: dict[str, bool | None]
    notes: list[str]

    @property
    def passed(self) -> bool:
        return all(v for v in self.checks.values() if v is not None)


def _citations_ok(a: Answer, book: FactBook, expected_ids: list[str]) -> tuple[bool, list[str]]:
    notes: list[str] = []
    if sorted(e.evidence_id for e in a.evidence) != sorted(expected_ids):
        notes.append(f"cited {[e.evidence_id for e in a.evidence]}, expected {expected_ids}")
        return False, notes
    for e in a.evidence:
        facts = book.facts_by_id(e.evidence_id)
        if len(facts) != 1:
            notes.append(f"{e.evidence_id} resolves to {len(facts)} facts")
            return False, notes
        f = facts[0]
        if (f.normalized_value, f.locator, f.source_checksum) != (e.normalized_value, e.locator, e.source_checksum):
            notes.append(f"{e.evidence_id} differs from the fact table")
            return False, notes
        if e.raw_amount_text not in e.amount_excerpt:
            notes.append(f"{e.evidence_id}: row text does not contain the amount")
            return False, notes
    return True, notes


def score_case(case: dict[str, Any], base: FactBook, snapshot: dict[str, Any] | None) -> CaseResult:
    variant = case.get("variant")
    table = apply_variant(variant, base.table)
    book = rebook(base, table, variant, snapshot) if variant else base
    a = answer_question(case["question"], book)
    expect_answered = case["expected_statuses"] == ["answered"]
    notes: list[str] = []
    checks: dict[str, bool | None] = {"value": None, "calculation": None, "citation": None, "non_answer": None, "narrative": None}
    if expect_answered:
        ok_status = a.status == "answered"
        if not ok_status:
            notes.append(f"expected answered, got {a.status} ({a.reason_code})")
        checks["value"] = ok_status
        checks["citation"] = ok_status
        if case["category"] == "change":
            checks["calculation"] = ok_status
        if ok_status and a.value is not None:
            if case["category"] == "lookup":
                checks["value"] = a.value.display == case["expected_display"]  # type: ignore[union-attr]
                if not checks["value"]:
                    notes.append(f"value {a.value.display} != {case['expected_display']}")  # type: ignore[union-attr]
            else:
                v, c = a.value, a.calculation
                good_abs = v.absolute_change_usd == Decimal(case["expected_absolute_usd"])  # type: ignore[union-attr]
                good_dir = v.direction == case["expected_direction"]  # type: ignore[union-attr]
                if case["expected_percent_display"] is not None:
                    good_pct = v.percent_change_display == case["expected_percent_display"]  # type: ignore[union-attr]
                else:
                    good_pct = (v.percent_change_display is None and c is not None  # type: ignore[union-attr]
                                and c.percent_withheld_reason_code == case["expected_percent_withheld_code"])
                checks["value"] = good_abs and good_dir
                checks["calculation"] = bool(good_abs and good_dir and good_pct and c is not None and validate_calculation(c) == [])
                if not checks["calculation"]:
                    notes.append(f"calculation: abs={good_abs} dir={good_dir} pct={good_pct}")
            checks["citation"], more = _citations_ok(a, book, case["expected_evidence_ids"])
            notes += more
        narrative = run_narrative(a, ScriptedAdapter()) if ok_status else None
        checks["narrative"] = None if narrative is None else narrative.status == "validated"
        if narrative is not None and narrative.status != "validated":
            notes.append("scripted narrative: " + ",".join(v.code for v in narrative.violations))
    else:
        ok_status = a.status in case["expected_statuses"]
        ok_code = a.reason_code in case["allowed_reason_codes"]
        checks["non_answer"] = ok_status and ok_code
        checks["citation"] = a.value is None and not a.evidence and a.calculation is None
        if not checks["non_answer"]:
            notes.append(f"expected {case['expected_statuses']} {case['allowed_reason_codes']}, got {a.status} ({a.reason_code})")
    return CaseResult(case["id"], case["group"], case["category"], expect_answered, a.status, a.reason_code, checks, notes)


def tally(results: list[CaseResult], key: str, subset: list[CaseResult] | None = None) -> dict[str, int]:
    pool = [r for r in (subset if subset is not None else results) if r.checks[key] is not None]
    return {"passed": sum(1 for r in pool if r.checks[key]), "denominator": len(pool)}


def evaluate_answers(root: Path, book: FactBook, snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    doc, info = load_cases(root)
    results = [score_case(c, book, snapshot) for c in doc["cases"]]
    expected_answered = [r for r in results if r.expected_answered]
    expected_non = [r for r in results if not r.expected_answered]
    false_answers = [r.id for r in expected_non if r.status == "answered"]
    false_non_answers = [r.id for r in expected_answered if r.status != "answered"]
    report = {
        "report_version": 1,
        "scope": doc["scope"], "authorship": doc["authorship"],
        "cases": {**info, "count": len(results)},
        "verification_level": book.level, "fact_table_source_checksum": book.table.source.checksum,
        "dimensions": {
            "1_value_correctness": {**tally(results, "value"), "meaning": "expected-answered cases returning exactly the expected value"},
            "2_calculation_correctness": {**tally(results, "calculation"), "meaning": "change cases answered with correct absolute, direction, percentage/withheld reason and a recomputing record"},
            "3_citation_validity": {**tally(results, "citation"), "of_which_answered": tally(results, "citation", expected_answered),
                                    "of_which_non_answers": tally(results, "citation", expected_non),
                                    "meaning": "answered: exact expected evidence IDs resolving to one matching fact whose row text holds the amount; non-answers: no value, evidence or calculation"},
            "4_appropriate_non_answers": {**tally(results, "non_answer"), "false_answers": false_answers, "false_non_answers": false_non_answers,
                                           "meaning": "expected-unresolved/refused cases returning an allowed status and reason code"},
            "5_scripted_narrative_validation": {**tally(results, "narrative"), "meaning": "scripted template narrative accepted by the deterministic validator (not a model)"},
        },
        "by_group": {g: {"cases": sum(1 for r in results if r.group == g), "all_checks_passed": sum(1 for r in results if r.group == g and r.passed)}
                     for g in sorted({r.group for r in results})},
        "cases_with_all_applicable_checks_passed": {"passed": sum(1 for r in results if r.passed), "denominator": len(results)},
        "failures": [{"id": r.id, "category": r.category, "status": r.status, "reason_code": r.reason_code, "notes": r.notes} for r in results if not r.passed],
        "per_case": [{"id": r.id, "group": r.group, "category": r.category, "status": r.status, "reason_code": r.reason_code,
                      "checks": r.checks, "passed": r.passed} for r in results],
    }
    return report


def render_report(rep: dict[str, Any]) -> str:
    d = rep["dimensions"]
    cases = rep["cases"]
    lines = [f"Answer evaluation: {cases['count']} frozen cases (hash {cases['cases_sha256'][:12]}..., "
             f"{'matches' if cases['matches_freeze_record'] else 'DOES NOT MATCH'} the freeze record)",
             f"Verification level of the evaluated fact table: {rep['verification_level']}", f"Scope: {rep['scope']}",
             f"Authorship: {rep['authorship']}", ""]
    for k, v in d.items():
        lines.append(f"  {k:36s} {v['passed']}/{v['denominator']}")
    da = d["4_appropriate_non_answers"]
    lines += [f"  false answers: {da['false_answers'] or 'none'}; false non-answers: {da['false_non_answers'] or 'none'}",
              f"  cases with every applicable check passed: {rep['cases_with_all_applicable_checks_passed']['passed']}/{rep['cases_with_all_applicable_checks_passed']['denominator']}"]
    for g, v in rep["by_group"].items():
        lines.append(f"    {g}: {v['all_checks_passed']}/{v['cases']}")
    if rep["failures"]:
        lines.append("Failures:")
        lines += [f"  {f['id']} ({f['category']}): {'; '.join(f['notes']) or f['status']}" for f in rep["failures"]]
    return "\n".join(lines) + "\n"
