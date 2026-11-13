"""Tests for the deterministic answer/calculation layer (src/finreport/answers.py).

Two kinds of data: (1) the REAL fact table from the first real-filing run (tests/real/msft_fy2024/fact_table.json; amounts below
are written out as literals taken from the handoff, never read from the evaluation expectations), and (2) synthetic fact tables
built from the project's fictional-company helpers so that edge cases (zero/negative prior, scale, tampering) can be constructed.
These are exposed development tests, not held-out evidence.
"""

import hashlib
import json
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from finreport.answers import (Answer, AnswerValidationError, ChangeValue, EvidenceRef, FactBook, FactBookError, answer_question,
                               answer_to_json, build_calculation, fact_integrity_problems, format_usd, load_factbook, parse_question,
                               percent_change, percent_display, validate_answer, validate_calculation, validate_comparable)
from finreport.cli import main
from finreport.extract import extract_facts
from finreport.ingest import verify_cached_source
from finreport.manifest import load_manifest
from finreport.schemas import FactTable, Period
from helpers import CHECKSUM, TARGET, standard_doc
from test_review_findings import synthetic_microsoft_shaped_root

ROOT = Path(__file__).parents[2]
REAL_TABLE = ROOT / "tests" / "real" / "msft_fy2024" / "fact_table.json"
REAL_RUN = ROOT / "review" / "real_run_packet" / "run"

Q1 = "What was Microsoft's consolidated revenue in FY2024?"
Q2 = "How much did consolidated revenue change from FY2023 to FY2024?"
Q3 = "What will Microsoft's consolidated revenue be in FY2027?"

# literals from the first real-filing run's handoff (USD millions, consolidated, US GAAP)
REAL = {"revenue": (245122, 211915), "net_income": (88136, 72361), "operating_cash_flow": (118548, 87582)}


def verified(table: FactTable) -> dict:
    info = {"filename": "f.htm", "byte_count": 1, "sha256": table.source.checksum}
    return {"verified": True, "problems": [], "recorded": dict(info), "actual": dict(info)}


def real_table() -> FactTable:
    return FactTable.model_validate_json(REAL_TABLE.read_text(encoding="utf-8"))


def real_book() -> FactBook:
    t = real_table()
    return FactBook.from_table(t, verified(t))


def synth_table() -> FactTable:
    return extract_facts(standard_doc(), TARGET, CHECKSUM)  # fictional company: revenue 1000 / 900, net income 250 / 200, OCF 400 / 350


def book_of(table: FactTable, **kw) -> FactBook:
    return FactBook.from_table(table, verified(table), **kw)


def edit(table: FactTable, evidence_id: str, **updates) -> FactTable:
    """Copy of the table with one fact changed WITHOUT revalidation (so inconsistent or tampered facts can be constructed)."""
    return table.model_copy(update={"facts": [f.model_copy(update=updates) if f.evidence_id == evidence_id else f for f in table.facts]})


def set_amount(table: FactTable, evidence_id: str, reported: str, scale: int = 1_000_000) -> FactTable:
    r = Decimal(reported)
    return edit(table, evidence_id, reported_value=r, scale_multiplier=scale, normalized_value=r * scale)


def fact(table: FactTable, evidence_id: str):
    return next(f for f in table.facts if f.evidence_id == evidence_id)


# =============================================================== provenance of the real fixture


def test_the_real_fact_table_fixture_is_the_one_from_the_received_packet():
    recorded = (REAL_TABLE.parent / "fact_table.sha256").read_text().split()[0]
    assert hashlib.sha256(REAL_TABLE.read_bytes()).hexdigest() == recorded
    t = real_table()
    assert t.source.checksum == "b0f255f67b2c26da21c69e05021ea333f4d42bafd415b153e8835a3ced2967fa"
    assert t.source.accession == "0000950170-24-087843" and len(t.facts) == 6 and not t.issues and not t.conflicts


# =============================================================== the three required answers, on the real amounts


def test_q1_lookup_revenue_fy2024():
    a = answer_question(Q1, real_book())
    assert (a.status, a.question_kind, a.reason_code) == ("answered", "lookup", None)
    assert a.value.normalized_usd == Decimal(REAL["revenue"][0]) * 1_000_000
    assert (a.value.reported_amount, a.value.reported_scale_multiplier, a.value.display) == (Decimal(245122), 1_000_000, "USD 245,122 million")
    assert [e.evidence_id for e in a.evidence] == ["REV_2024"]
    ref = a.evidence[0]
    assert (ref.period_label, ref.scope, ref.accounting_basis, ref.currency, ref.currency_evidence_kind) == (
        "FY2024", "consolidated", "US GAAP", "USD", "ixbrl_unit_usd")
    assert (ref.locator.statement, ref.locator.printed_page, ref.locator.table_index, ref.locator.row_index, ref.locator.cell_index) == (
        "INCOME STATEMENTS", 56, 24, 11, 3)
    assert ref.source_checksum == real_table().source.checksum and ref.amount_excerpt.startswith("Total revenue |")
    assert a.calculation is None and a.source.source_verified is True
    assert any("10-K/A" in s and "not been performed" in s for s in a.limitations)
    assert validate_answer(a, real_book(), [("revenue", "FY2024")]) == []


def test_q2_change_revenue_fy2023_to_fy2024():
    a = answer_question(Q2, real_book())
    assert (a.status, a.question_kind) == ("answered", "change")
    v: ChangeValue = a.value
    assert v.absolute_change_usd == Decimal(33_207_000_000) and v.absolute_change_display == "USD 33,207 million"
    assert v.direction == "increase" and v.percent_change_display == "15.67%" and v.percent_withheld_reason is None
    assert v.percent_change_exact == Decimal("15.66996201307127857867541231")  # independently computed by the reviewer with Decimal
    assert [e.evidence_id for e in a.evidence] == ["REV_2023", "REV_2024"]  # prior first, then current
    calc = a.calculation
    assert [(i.role, i.evidence_id, i.normalized_value) for i in calc.inputs] == [
        ("prior", "REV_2023", Decimal(211_915_000_000)), ("current", "REV_2024", Decimal(245_122_000_000))]
    assert calc.steps[0] == "absolute_change = current - prior = 245122000000 - 211915000000 = 33207000000"
    assert calc.steps[-1] == "displayed to two decimal places, ROUND_HALF_UP = 15.67%"
    assert validate_calculation(calc) == [] and validate_answer(a, real_book(), [("revenue", "FY2023"), ("revenue", "FY2024")]) == []


def test_q3_forecast_is_unresolved_with_no_number_and_no_citation():
    a = answer_question(Q3, real_book())
    assert (a.status, a.question_kind, a.reason_code) == ("unresolved", "unsupported", "forecast_not_supported")
    assert a.value is None and a.evidence == [] and a.calculation is None
    assert "FY2027" in a.reason and "FY2024" in a.reason and "No forecast is made" in a.reason
    dumped = json.loads(a.model_dump_json())
    assert dumped["evidence"] == [] and dumped["value"] is None and dumped["calculation"] is None
    assert not any(token in a.model_dump_json() for token in ("REV_2", "245122", "211915", "33207"))


@pytest.mark.parametrize("metric,label", [("net_income", "net income"), ("operating_cash_flow", "operating cash flow"),
                                          ("revenue", "revenue")])
def test_every_real_metric_looks_up_and_changes_correctly(metric, label):
    book = real_book()
    cur, pri = REAL[metric]
    a24 = answer_question(f"What was {label} in FY2024?", book)
    a23 = answer_question(f"What was {label} in FY2023?", book)
    assert a24.value.normalized_usd == cur * 1_000_000 and a23.value.normalized_usd == pri * 1_000_000
    ch = answer_question(f"How much did {label} change from FY2023 to FY2024?", book)
    assert ch.value.absolute_change_usd == (cur - pri) * 1_000_000
    expected_pct = ((Decimal(cur) - Decimal(pri)) / Decimal(pri) * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    assert ch.value.percent_change_display == f"{expected_pct}%"
    assert ch.value.direction == "increase"


@pytest.mark.parametrize("text", [
    "What was Microsoft's consolidated revenue in FY2024?",
    "what was revenue in fy2024",
    "WHAT WAS THE REVENUE FOR FISCAL YEAR 2024?",
    "What was Microsoft Corporation's total revenue during FY2024.",
    "  What was the company's consolidated total revenues in fiscal 2024?  ",
])
def test_phrasing_variants_of_the_lookup(text):
    a = answer_question(text, real_book())
    assert a.status == "answered" and a.evidence[0].evidence_id == "REV_2024"


@pytest.mark.parametrize("text", [
    "How much did consolidated revenue change from FY2023 to FY2024?",
    "how much did microsoft's revenue change between fy2023 and fy2024?",
    "What was the change in consolidated revenue from FY2023 to FY2024?",
    "What is the difference in revenue between fiscal year 2023 and fiscal year 2024",
])
def test_phrasing_variants_of_the_change_question(text):
    a = answer_question(text, real_book())
    assert a.status == "answered" and a.value.absolute_change_display == "USD 33,207 million"


@pytest.mark.parametrize("text,metric", [("What was net cash from operations in FY2024?", "operating_cash_flow"),
                                         ("What was net earnings in FY2024?", "net_income")])
def test_metric_aliases(text, metric):
    assert answer_question(text, real_book()).value.metric == metric


def test_change_in_the_reverse_direction_is_a_decrease_from_the_first_named_year():
    a = answer_question("How much did revenue change from FY2024 to FY2023?", real_book())
    assert a.value.direction == "decrease" and a.value.absolute_change_usd == Decimal(-33_207_000_000)
    assert a.value.percent_change_display == f"{((Decimal(211915) - Decimal(245122)) / Decimal(245122) * 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}%"
    assert [e.evidence_id for e in a.evidence] == ["REV_2024", "REV_2023"]


# =============================================================== unsupported questions never invent anything


@pytest.mark.parametrize("text,code", [
    ("What will revenue be in FY2027?", "forecast_not_supported"),
    ("Forecast Microsoft's revenue for FY2025", "forecast_not_supported"),
    ("What is Microsoft expected to earn next year?", "forecast_not_supported"),
    ("What will net income be in FY2026?", "forecast_not_supported"),
    ("What was revenue in FY2025?", "forecast_not_supported"),       # after the evidenced years
    ("How much did revenue change from FY2024 to FY2026?", "forecast_not_supported"),
    ("What was operating income in FY2024?", "metric_not_in_evidence"),
    ("What was the gross margin in FY2024?", "metric_not_in_evidence"),
    ("What was revenue in FY2022?", "period_not_in_evidence"),        # a metric we have, a year we did not extract
    ("How much did revenue change from FY2022 to FY2024?", "period_not_in_evidence"),
    ("What was Apple's revenue in FY2024?", "unsupported_question"),
    ("What was revenue in 2024?", "unsupported_question"),           # bare year: calendar or fiscal is ambiguous
    ("Tell me about Microsoft", "unsupported_question"),
    ("", "unsupported_question"),
    ("'; DROP TABLE facts; --", "unsupported_question"),
])
def test_unsupported_and_unresolvable_questions_cite_nothing(text, code):
    a = answer_question(text, real_book())
    assert a.status == "unresolved" and a.reason_code == code and a.reason
    assert a.value is None and a.evidence == [] and a.calculation is None
    assert validate_answer(a, real_book()) == []


def test_a_metric_with_no_facts_at_all_is_not_in_evidence_while_a_missing_year_is_a_different_reason():
    table = synth_table()
    no_ocf = table.model_copy(update={"facts": [f for f in table.facts if f.metric != "operating_cash_flow"]})
    book = book_of(no_ocf)
    a = answer_question("What was operating cash flow in FY2024?", book)
    assert (a.status, a.reason_code, a.evidence) == ("unresolved", "metric_not_in_evidence", [])
    only_2024 = table.model_copy(update={"facts": [f for f in table.facts if not (f.metric == "revenue" and f.period.label == "FY2023")]})
    b = answer_question("What was revenue in FY2023?", book_of(only_2024))
    assert (b.reason_code, b.evidence) == ("period_not_in_evidence", [])  # the metric exists for another year
    c = answer_question("How much did revenue change from FY2023 to FY2024?", book_of(only_2024))
    assert (c.status, c.reason_code, c.evidence, c.calculation) == ("unresolved", "period_not_in_evidence", [], None)


def test_unsupported_questions_do_not_need_verified_evidence_and_say_so():
    t = real_table()
    book = FactBook.from_table(t, source_check=None)
    a = answer_question(Q3, book)
    assert a.status == "unresolved" and a.source.source_verified is False and "source_not_verified" in a.source.verification_problems


# =============================================================== calculation rules


def change(table: FactTable, text="How much did revenue change from FY2023 to FY2024?") -> Answer:
    return answer_question(text, book_of(table))


def test_zero_prior_amount_returns_the_absolute_change_and_withholds_the_percentage():
    t = set_amount(synth_table(), "REV_2023", "0")
    a = change(t)
    assert a.status == "answered" and a.value.absolute_change_usd == Decimal(1000) * 1_000_000
    assert a.value.percent_change_exact is None and a.value.percent_change_display is None
    assert a.calculation.percent_withheld_reason_code == "prior_period_zero" and "zero" in a.value.percent_withheld_reason
    assert validate_calculation(a.calculation) == []
    assert validate_answer(a, book_of(t), [("revenue", "FY2023"), ("revenue", "FY2024")]) == []  # valid against the table it was built from


def test_negative_prior_amount_withholds_the_percentage_with_the_right_reason():
    a = change(set_amount(synth_table(), "REV_2023", "-100"))
    assert a.value.absolute_change_usd == Decimal(1100) * 1_000_000
    assert a.value.percent_change_display is None and a.calculation.percent_withheld_reason_code == "prior_period_negative"
    assert "negative" in a.value.percent_withheld_reason and validate_calculation(a.calculation) == []


def test_positive_prior_with_negative_current_gives_a_negative_percentage():
    a = change(set_amount(synth_table(), "REV_2024", "-50"))
    assert a.value.direction == "decrease" and a.value.percent_change_display == "-105.56%"
    assert a.value.absolute_change_usd == Decimal(-950) * 1_000_000


@pytest.mark.parametrize("prior,current,display", [("800", "801", "0.13%"),      # exactly 0.125 -> HALF_UP gives 0.13 (HALF_EVEN would give 0.12)
                                                   ("800", "799", "-0.13%"),     # exactly -0.125 -> away from zero
                                                   ("200", "201", "0.50%"), ("3", "4", "33.33%"), ("3", "5", "66.67%")])
def test_percentage_display_is_rounded_half_up_to_two_places(prior, current, display):
    t = set_amount(set_amount(synth_table(), "REV_2023", prior), "REV_2024", current)
    assert change(t).value.percent_change_display == display


def test_exact_percentage_uses_28_significant_digits_independently_checked():
    exact, code, why = percent_change(Decimal(211915000000), Decimal(245122000000))
    assert (code, why) == (None, None) and exact == Decimal("15.66996201307127857867541231") and percent_display(exact) == "15.67%"
    assert len(str(exact).replace(".", "").lstrip("0")) == 28


def test_equal_amounts_are_no_change_and_zero_percent():
    t = set_amount(set_amount(synth_table(), "REV_2023", "500"), "REV_2024", "500")
    a = change(t)
    assert a.value.direction == "no_change" and a.value.absolute_change_usd == 0 and a.value.percent_change_display == "0.00%"


def test_facts_in_different_scales_are_compared_on_normalized_amounts():
    t = set_amount(synth_table(), "REV_2023", "900000", scale=1_000)  # 900,000 thousand = 900 million, reported in thousands
    a = change(t)
    assert a.status == "answered" and a.value.absolute_change_usd == Decimal(100) * 1_000_000
    assert a.value.percent_change_display == "11.11%"
    assert a.value.absolute_change_display == "USD 100,000,000"  # scales differ, so no single unit word is claimed


def test_a_normalized_amount_inconsistent_with_reported_and_scale_is_refused():
    t = edit(synth_table(), "REV_2024", normalized_value=Decimal(1000))  # reported 1000 x 1,000,000 != 1000
    a = change(t)
    assert a.status == "refused" and a.reason_code == "evidence_not_verified"
    assert "normalized_value_inconsistent_with_reported_value_and_scale" in a.reason and a.evidence == []


def test_the_fact_scale_multiplier_must_be_a_supported_scale():
    t = edit(synth_table(), "REV_2024", scale_multiplier=7, normalized_value=Decimal(7000))
    assert change(t).status == "refused"


# =============================================================== comparability


def comparable_pair(**changes):
    t = synth_table()
    return fact(t, "REV_2023"), fact(t, "REV_2024").model_copy(update=changes)


def test_comparable_facts_have_no_problems():
    assert validate_comparable(*comparable_pair()) == []


@pytest.mark.parametrize("changes,code", [
    ({"metric": "net_income"}, "metric_mismatch"),
    ({"scope": "segment"}, "scope_mismatch"),
    ({"accounting_basis": "non-GAAP"}, "accounting_basis_mismatch"),
    ({"currency": "EUR"}, "currency_mismatch"),
    ({"accession": "0000000000-24-000001"}, "source_filing_mismatch"),
    ({"source_checksum": "ee" * 32}, "source_filing_mismatch"),
    ({"period": Period(label="FY2023", start=date(2022, 7, 1), end=date(2023, 6, 30))}, "same_period"),
    ({"period": Period.model_construct(label="FY2024", start=date(2023, 7, 1), end=date(2024, 6, 30), type="quarterly")}, "period_not_annual"),
])
def test_incomparable_facts_are_named(changes, code):
    assert code in validate_comparable(*comparable_pair(**changes))


def test_period_length_must_be_comparable_even_if_both_are_marked_annual():
    from datetime import date
    # model_construct bypasses the schema's own 364..371-day rule, simulating a tampered or corrupted period
    three_years = Period.model_construct(label="FY2024", start=date(2022, 1, 1), end=date(2024, 12, 31), type="annual_duration")
    nine_months = Period.model_construct(label="FY2024", start=date(2024, 1, 1), end=date(2024, 9, 30), type="annual_duration")
    calendar_year = Period(label="FY2024", start=date(2024, 1, 1), end=date(2024, 12, 31))
    assert "period_length_incomparable" in validate_comparable(*comparable_pair(period=three_years))
    assert "period_length_incomparable" in validate_comparable(*comparable_pair(period=nine_months))
    assert "period_length_incomparable" not in validate_comparable(*comparable_pair(period=calendar_year))  # 366 vs 365 days is fine


def test_a_period_not_marked_annual_is_refused_by_the_integrity_check():
    t = synth_table()
    bad = edit(t, "REV_2024", period=Period.model_construct(label="FY2024", start=fact(t, "REV_2024").period.start,
                                                            end=fact(t, "REV_2024").period.end, type="quarterly"))
    assert "period_not_annual" in fact_integrity_problems(fact(bad, "REV_2024"), book_of(bad))
    assert change(bad).status == "refused"


def test_a_52_53_week_year_is_comparable_to_a_52_week_year():
    from datetime import date
    wk53 = Period(label="FY2024", start=date(2023, 9, 24), end=date(2024, 9, 28))
    assert (wk53.end - wk53.start).days + 1 == 371
    p = comparable_pair(period=wk53)
    assert "period_length_incomparable" not in validate_comparable(*p)


def test_end_to_end_incomparable_facts_are_refused_not_computed():
    t = edit(synth_table(), "REV_2024", accounting_basis="non-GAAP")
    a = change(t)
    assert a.status == "refused" and a.reason_code == "facts_not_comparable" and "accounting_basis_mismatch" in a.reason
    assert a.value is None and a.evidence == [] and a.calculation is None


def test_facts_from_two_different_filings_are_never_combined():
    t = edit(synth_table(), "REV_2023", accession="0000000000-23-000001", source_checksum="aa" * 32)
    a = change(t)
    assert a.status == "refused"  # rejected at the fact-integrity step (its source differs from the table's source)


# =============================================================== evidence problems refuse the answer


def test_a_fact_flagged_with_a_conflict_is_not_used():
    t = edit(synth_table(), "REV_2024", conflict_status="authoritative_retained_with_disagreeing_occurrences")
    a = answer_question("What was revenue in FY2024?", book_of(t))
    assert a.status == "refused" and "fact_flagged_with_conflict" in a.reason


def test_an_unresolved_conflict_for_the_metric_and_period_refuses():
    t = synth_table()
    conflict = {"kind": "corroboration_mismatch", "metric": "revenue", "period_label": "FY2024", "scope": "consolidated",
                "accounting_basis": "US GAAP", "resolved": False, "decision": "x", "occurrences": []}
    t = t.model_copy(update={"conflicts": [type(t).model_fields["conflicts"].annotation.__args__[0].model_validate(conflict)]})
    assert answer_question("What was revenue in FY2024?", book_of(t)).status == "refused"
    assert answer_question("What was net income in FY2024?", book_of(t)).status == "answered"  # other metrics are unaffected


def test_a_blocking_issue_for_the_metric_refuses_but_a_non_blocking_one_does_not():
    from finreport.schemas import Issue
    t = synth_table()
    blocking = t.model_copy(update={"issues": [Issue(code="missing_amount", message="m", metric="revenue", period_label="FY2024", blocking=True)]})
    soft = t.model_copy(update={"issues": [Issue(code="missing_amount", message="m", metric="revenue", period_label="FY2024", blocking=False)]})
    assert answer_question("What was revenue in FY2024?", book_of(blocking)).status == "refused"
    assert answer_question("What was revenue in FY2024?", book_of(soft)).status == "answered"


def test_a_non_consolidated_or_non_usd_fact_is_refused():
    assert answer_question("What was revenue in FY2024?", book_of(edit(synth_table(), "REV_2024", scope="segment"))).status == "refused"
    assert answer_question("What was revenue in FY2024?", book_of(edit(synth_table(), "REV_2024", currency="EUR"))).status == "refused"


def test_two_facts_for_the_same_metric_and_period_are_ambiguous_not_guessed():
    t = synth_table()
    dup = fact(t, "REV_2024").model_copy(update={"evidence_id": "REV_2024_b"})
    a = answer_question("What was revenue in FY2024?", book_of(t.model_copy(update={"facts": [*t.facts, dup]})))
    assert a.status == "unresolved" and a.reason_code == "ambiguous_evidence" and a.evidence == []


def test_an_unverified_source_refuses_answers_that_need_evidence():
    t = synth_table()
    for check, expect in [(None, "source_not_verified"),
                          ({"verified": False, "problems": ["sha256_mismatch"], "recorded": None, "actual": None}, "source:sha256_mismatch")]:
        book = FactBook.from_table(t, check)
        for q in ("What was revenue in FY2024?", "How much did revenue change from FY2023 to FY2024?"):
            a = answer_question(q, book)
            assert a.status == "refused" and a.reason_code == "source_not_verified" and expect in a.reason and a.evidence == []


def test_a_fact_table_built_from_other_bytes_than_the_verified_file_is_unverified():
    t = synth_table()
    other = verified(t)
    other["actual"]["sha256"] = "dd" * 32
    assert "table_checksum_differs_from_verified_file" in FactBook.from_table(t, other).problems


def test_a_fact_whose_checksum_differs_from_the_table_is_flagged_by_the_book():
    t = edit(synth_table(), "REV_2024", source_checksum="ee" * 32)
    assert any(p.startswith("fact_checksum_mismatch") for p in FactBook.from_table(t, verified(t)).problems)


# =============================================================== evidence-reference validators


def good_answer(table=None):
    book = book_of(table or synth_table())
    return answer_question("How much did revenue change from FY2023 to FY2024?", book), book


def test_a_valid_answer_validates_clean():
    a, book = good_answer()
    assert validate_answer(a, book, [("revenue", "FY2023"), ("revenue", "FY2024")]) == []


def test_unknown_evidence_ids_do_not_resolve():
    a, book = good_answer()
    bad = a.model_copy(update={"evidence": [a.evidence[0].model_copy(update={"evidence_id": "REV_2099"}), a.evidence[1]]})
    assert any(p.startswith("evidence_reference_does_not_resolve_to_one_fact:REV_2099") for p in validate_answer(bad, book))


def test_duplicate_evidence_references_are_reported():
    a, book = good_answer()
    bad = a.model_copy(update={"evidence": [a.evidence[0], a.evidence[0]]})
    assert any(p.startswith("duplicate_evidence_reference") for p in validate_answer(bad, book))


@pytest.mark.parametrize("field,value", [("reported_value", Decimal(901)), ("normalized_value", Decimal(901_000_000)),
                                         ("period_label", "FY2022"), ("scope", "segment"), ("accounting_basis", "IFRS"),
                                         ("source_checksum", "ee" * 32), ("raw_amount_text", "$901"), ("scale_multiplier", 1000)])
def test_a_cited_field_that_differs_from_the_fact_table_is_caught(field, value):
    a, book = good_answer()
    bad = a.model_copy(update={"evidence": [a.evidence[0].model_copy(update={field: value}), a.evidence[1]]})
    assert any(p.startswith(f"evidence_field_differs_from_fact_table:REV_2023:{field}") for p in validate_answer(bad, book))


def test_a_tampered_locator_is_caught():
    a, book = good_answer()
    loc = a.evidence[0].locator.model_copy(update={"printed_page": 999})
    bad = a.model_copy(update={"evidence": [a.evidence[0].model_copy(update={"locator": loc}), a.evidence[1]]})
    assert any("locator" in p for p in validate_answer(bad, book))


def test_the_cited_metric_and_years_must_be_the_ones_asked_for():
    a, book = good_answer()
    assert any(p.startswith("cited_evidence_is_not_the_requested_metric_and_periods") for p in
               validate_answer(a, book, [("revenue", "FY2022"), ("revenue", "FY2024")]))     # wrong year
    assert any(p.startswith("cited_evidence_is_not_the_requested_metric_and_periods") for p in
               validate_answer(a, book, [("net_income", "FY2023"), ("net_income", "FY2024")]))  # wrong metric


def test_calculation_inputs_must_be_exactly_the_cited_evidence_and_values():
    a, book = good_answer()
    swapped = a.calculation.model_copy(update={"inputs": [a.calculation.inputs[0].model_copy(update={"evidence_id": "NI_2023"}), a.calculation.inputs[1]]})
    assert "calculation_inputs_are_not_exactly_the_cited_evidence" in validate_answer(a.model_copy(update={"calculation": swapped}), book)
    off = a.calculation.model_copy(update={"inputs": [a.calculation.inputs[0].model_copy(update={"normalized_value": Decimal(1)}), a.calculation.inputs[1]]})
    problems = validate_answer(a.model_copy(update={"calculation": off}), book)
    assert any(p.startswith("calculation_input_differs_from_cited_value") for p in problems)


@pytest.mark.parametrize("update,problem", [
    ({"absolute_change_usd": Decimal(1)}, "absolute_change_does_not_equal_current_minus_prior"),
    ({"percent_change_exact": Decimal("11.11")}, "percent_change_does_not_match_recomputation"),
    ({"percent_change_display": "11.1%"}, "percent_display_does_not_match_rounding_rule"),
    ({"percent_withheld_reason_code": "prior_period_zero", "percent_withheld_reason": "x"}, "percent_has_a_withheld_reason"),
])
def test_a_tampered_calculation_is_caught_by_independent_recomputation(update, problem):
    a, _ = good_answer()
    assert problem in validate_calculation(a.calculation.model_copy(update=update))


def test_a_percentage_cannot_accompany_a_non_positive_prior_amount():
    a = change(set_amount(synth_table(), "REV_2023", "0"))
    forged = a.calculation.model_copy(update={"percent_change_exact": Decimal(5), "percent_change_display": "5.00%"})
    assert "percent_present_but_prior_amount_is_not_positive" in validate_calculation(forged)
    unexplained = a.calculation.model_copy(update={"percent_withheld_reason": None})
    assert "percent_withheld_without_the_correct_reason" in validate_calculation(unexplained)


def test_the_answer_value_must_match_its_calculation_and_cited_evidence():
    a, book = good_answer()
    v = a.value.model_copy(update={"absolute_change_usd": Decimal(5)})
    assert "answer_value_differs_from_calculation" in validate_answer(a.model_copy(update={"value": v}), book)
    lookup = answer_question("What was revenue in FY2024?", book)
    lv = lookup.value.model_copy(update={"normalized_usd": Decimal(1)})
    assert "lookup_value_differs_from_cited_evidence" in validate_answer(lookup.model_copy(update={"value": lv}), book)


def test_unresolved_and_refused_answers_cannot_be_built_with_a_citation_or_a_value():
    a, book = good_answer()
    with pytest.raises(ValidationError):
        Answer(question="q", question_kind="unsupported", status="unresolved", entity="e", evidence=a.evidence, reason_code="x", reason="y",
               limitations=[], source=a.source)
    with pytest.raises(ValidationError):
        Answer(question="q", question_kind="change", status="refused", entity="e", evidence=[], value=a.value, reason_code="x", reason="y",
               limitations=[], source=a.source)
    with pytest.raises(ValidationError):
        Answer(question="q", question_kind="lookup", status="unresolved", entity="e", evidence=[], reason_code=None, reason=None,
               limitations=[], source=a.source)


def test_an_answered_answer_needs_the_right_amount_of_evidence():
    a, book = good_answer()
    with pytest.raises(ValidationError):
        Answer(question="q", question_kind="lookup", status="answered", entity="e", evidence=a.evidence, value=a.value, limitations=[], source=a.source)
    with pytest.raises(ValidationError):
        Answer(question="q", question_kind="change", status="answered", entity="e", evidence=a.evidence[:1], value=a.value, limitations=[], source=a.source,
               calculation=a.calculation)


def test_an_answer_that_fails_its_own_validators_is_never_returned(monkeypatch):
    import finreport.answers as mod
    monkeypatch.setattr(mod, "validate_answer", lambda *a, **k: ["forced_problem"])
    with pytest.raises(AnswerValidationError, match="forced_problem"):
        answer_question("What was revenue in FY2024?", book_of(synth_table()))


# =============================================================== parsing and formatting units


def test_parse_question_shapes():
    assert parse_question(Q1, "Microsoft Corporation").fiscal_years == (2024,) and parse_question(Q1, "Microsoft Corporation").metric == "revenue"
    q = parse_question(Q2, "Microsoft Corporation")
    assert (q.kind, q.metric, q.fiscal_years) == ("change", "revenue", (2023, 2024))
    q = parse_question(Q3, "Microsoft Corporation")
    assert (q.kind, q.reason_code, q.fiscal_years, q.metric) == ("unsupported", "forecast_not_supported", (2027,), "revenue")


def test_the_entity_must_be_the_filings_company():
    assert parse_question("What was Examplecorp's revenue in FY2024?", "Examplecorp").kind == "lookup"
    assert parse_question("What was Microsoft's revenue in FY2024?", "Examplecorp").kind == "unsupported"


@pytest.mark.parametrize("value,scale,text", [(Decimal(245_122_000_000), 1_000_000, "USD 245,122 million"),
                                              (Decimal(-1_500_000_000), 1_000_000, "-USD 1,500 million"),
                                              (Decimal(12_000_000), 1_000, "USD 12,000 thousand"),
                                              (Decimal(3_000_000_000), 1_000_000_000, "USD 3 billion"),
                                              (Decimal(1_234_567), None, "USD 1,234,567")])
def test_format_usd(value, scale, text):
    assert format_usd(value, scale) == text


def test_build_calculation_records_every_step():
    t = synth_table()
    calc = build_calculation(fact(t, "REV_2023"), fact(t, "REV_2024"))
    assert calc.absolute_change_usd == Decimal(100) * 1_000_000 and calc.percent_change_display == "11.11%"
    assert len(calc.steps) == 3 and calc.precision_significant_digits == 28 and "ROUND_HALF_UP" in calc.display_rounding


# =============================================================== independence from the evaluation expectations


def test_the_answer_layer_never_reads_or_embeds_the_expected_answers():
    src = (ROOT / "src" / "finreport" / "answers.py").read_text()
    for forbidden in ("expected_facts", "evaluation/", "245122", "211915", "88136", "72361", "118548", "87582",
                      "245,122", "211,915", "88,136", "72,361", "118,548", "87,582"):
        assert forbidden not in src, forbidden
    import ast
    tree = ast.parse(src)
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not any("evaluation" in m for m in imported), imported  # a real import check, not just a text search


def test_answers_do_not_change_when_the_expectations_file_is_wrong_or_missing(tmp_path):
    root = tmp_path
    synthetic_microsoft_shaped_root(root, standard_doc())
    assert main(["--root", str(root), "extract"]) == 0
    expected = root / "evaluation" / "expected_facts.json"
    outputs = []
    for variant in ("present", "garbage", "missing"):
        if variant == "garbage":
            expected.write_text('{"facts": "not what you think"}')
        elif variant == "missing":
            expected.unlink()
        outputs.append(answer_to_json(answer_question("What was revenue in FY2024?", load_factbook(root))))
    assert outputs[0] == outputs[1] == outputs[2]


# =============================================================== the CLI and the loader


def prepared_root(tmp_path: Path, capsys=None) -> Path:
    synthetic_microsoft_shaped_root(tmp_path, standard_doc())
    assert main(["--root", str(tmp_path), "extract"]) == 0
    if capsys is not None:
        capsys.readouterr()  # discard the setup commands' own output so only `ask` output is parsed
    return tmp_path


def ask(root: Path, question: str, capsys) -> tuple[int, dict]:
    code = main(["--root", str(root), "ask", question])
    out = capsys.readouterr().out
    return code, json.loads(out) if out.strip().startswith("{") else {}


def test_cli_exit_codes_follow_the_answer_status(tmp_path, capsys):
    root = prepared_root(tmp_path, capsys)
    code, a = ask(root, "What was revenue in FY2024?", capsys)
    assert (code, a["status"], a["value"]["normalized_usd"]) == (0, "answered", "1000000000")
    assert ask(root, "What will revenue be in FY2027?", capsys)[0] == 1
    code, a = ask(root, "How much did revenue change from FY2023 to FY2024?", capsys)
    assert code == 0 and a["value"]["percent_change_display"] == "11.11%"


def test_cli_ask_is_read_only(tmp_path, capsys):
    root = prepared_root(tmp_path, capsys)
    snapshot = lambda: sorted((str(p.relative_to(root)), p.stat().st_mtime_ns, p.stat().st_size) for p in root.rglob("*") if p.is_file())  # noqa: E731
    before = snapshot()
    ask(root, "What was revenue in FY2024?", capsys)
    assert snapshot() == before


def test_cli_out_writes_the_same_json_as_stdout(tmp_path, capsys):
    root = prepared_root(tmp_path, capsys)
    target = tmp_path / "answer.json"
    assert main(["--root", str(root), "ask", "What was revenue in FY2024?", "--out", str(target)]) == 0
    assert target.read_text() == capsys.readouterr().out


def test_cli_refuses_when_the_cached_source_changed_after_extraction(tmp_path, capsys):
    root = prepared_root(tmp_path, capsys)
    cached = next((root / "data" / "cache").glob("*.htm"))
    cached.write_bytes(cached.read_bytes()[:-1] + b"Z")
    code, a = ask(root, "What was revenue in FY2024?", capsys)
    assert code == 5 and a["status"] == "refused" and a["reason_code"] == "source_not_verified" and a["evidence"] == []
    assert "sha256_mismatch" in a["reason"] and a["source"]["source_verified"] is False


def test_cli_without_a_manifest_refuses_to_answer_from_evidence(tmp_path, capsys):
    root = prepared_root(tmp_path, capsys)
    (root / "data" / "source_manifest.json").unlink()
    code, a = ask(root, "What was revenue in FY2024?", capsys)
    assert code == 5 and a["reason_code"] == "source_not_verified"


def test_cli_exit_2_when_there_is_no_usable_fact_table(tmp_path, capsys):
    assert main(["--root", str(tmp_path), "ask", "What was revenue in FY2024?"]) == 2
    out = tmp_path / "output"
    out.mkdir()
    (out / "fact_table.json").write_text("{not json")
    assert main(["--root", str(tmp_path), "ask", "What was revenue in FY2024?"]) == 2
    (out / "fact_table.json").write_text(json.dumps({"schema_version": 1}))
    assert main(["--root", str(tmp_path), "ask", "What was revenue in FY2024?"]) == 2
    assert "ask stopped" in capsys.readouterr().err


def test_load_factbook_raises_for_unreadable_tables(tmp_path):
    with pytest.raises(FactBookError):
        load_factbook(tmp_path)


def test_a_fact_table_for_a_different_accession_than_the_manifest_is_unverified(tmp_path):
    root = prepared_root(tmp_path)
    p = root / "output" / "fact_table.json"
    d = json.loads(p.read_text())
    d["source"]["accession"] = "0000000000-24-000009"
    for f in d["facts"]:
        f["accession"] = "0000000000-24-000009"
    p.write_text(json.dumps(d))
    assert "table_accession_differs_from_manifest" in load_factbook(root).problems


# =============================================================== the real filing, end to end (offline, from the received packet)


@pytest.mark.skipif(not (REAL_RUN / "data" / "cache" / "msft-20240630.htm").exists(), reason="received real-filing packet not present")
def test_the_three_questions_end_to_end_on_the_received_real_filing(capsys):
    manifest = load_manifest(REAL_RUN / "data" / "source_manifest.json")
    assert verify_cached_source(REAL_RUN, manifest)["verified"] is True  # 6,861,379 bytes, SHA-256 b0f255f6...
    code, a1 = ask(REAL_RUN, Q1, capsys)
    code2, a2 = ask(REAL_RUN, Q2, capsys)
    code3, a3 = ask(REAL_RUN, Q3, capsys)
    assert (code, code2, code3) == (0, 0, 1)
    assert a1["value"]["display"] == "USD 245,122 million" and a1["source"]["source_verified"] is True
    assert a2["value"]["absolute_change_display"] == "USD 33,207 million" and a2["value"]["percent_change_display"] == "15.67%"
    assert a3["status"] == "unresolved" and a3["evidence"] == [] and a3["reason_code"] == "forecast_not_supported"
    assert a1["source"]["checksum"] == "b0f255f67b2c26da21c69e05021ea333f4d42bafd415b153e8835a3ced2967fa"
