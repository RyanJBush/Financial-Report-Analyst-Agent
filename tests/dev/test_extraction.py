from decimal import Decimal

from helpers import (CHECKSUM, TARGET, cashflow, comprehensive, income, make_doc, row, standard_doc, statement)

from finreport.extract import extract_facts
from finreport.parsing import parse_document, resolve_locator
from finreport.schemas import EXCERPT_KINDS


def run(html: bytes):
    return extract_facts(html, TARGET, CHECKSUM)


def by_id(table):
    return {f.evidence_id: f for f in table.facts}


def codes(table):
    return sorted(i.code for i in table.issues)


# ------------------------------------------------------------------ happy path


def test_six_facts_with_complete_evidence():
    table = run(standard_doc())
    facts = by_id(table)
    assert sorted(facts) == ["NI_2023", "NI_2024", "OCF_2023", "OCF_2024", "REV_2023", "REV_2024"]
    assert table.issues == [] and table.conflicts == []
    rev = facts["REV_2024"]
    assert (rev.reported_value, rev.normalized_value, rev.scale_multiplier) == (Decimal(1000), Decimal(1_000_000_000), 1_000_000)
    assert (str(rev.period.start), str(rev.period.end), rev.period.label) == ("2023-07-01", "2024-06-30", "FY2024")
    assert rev.scope == "consolidated" and rev.accounting_basis == "US GAAP" and rev.currency == "USD"
    assert {e.kind for e in rev.excerpts} >= set(EXCERPT_KINDS)
    assert rev.locator.section == "Part II, Item 8"
    assert rev.locator.statement == "INCOME STATEMENTS" and rev.locator.printed_page == 3
    assert rev.locator.column == "2024" and rev.locator.row == "Total revenue"
    assert facts["OCF_2023"].normalized_value == Decimal(350_000_000)
    assert facts["OCF_2024"].locator.printed_page == 5


def test_locators_resolve_to_the_raw_amount_text():
    html = standard_doc()
    doc = parse_document(html)
    for fact in run(html).facts:
        loc = fact.locator
        assert fact.raw_amount_text.lstrip("$") in resolve_locator(doc, loc.table_index, loc.row_index, loc.cell_index) or \
            resolve_locator(doc, loc.table_index, loc.row_index, loc.cell_index) in fact.raw_amount_text


def test_element_id_is_attached_when_present():
    doc = make_doc(income([row("Total revenue", ["1,000", "900"], dollar=True, row_id="rev_row"),
                            row("Net income", ["250", "200"], dollar=True)]), cashflow())
    assert by_id(run(doc))["REV_2024"].locator.element_id == "rev_row"


# ------------------------------------------------------------------ wrong year


def test_columns_are_chosen_by_header_not_position():
    reversed_income = statement("INCOME STATEMENTS", [row("Total revenue", ["900", "1,000"], dollar=True),
                                                      row("Net income", ["200", "250"], dollar=True)],
                                years=("2023", "2024"), page=3)
    facts = by_id(run(make_doc(reversed_income, cashflow())))
    assert facts["REV_2024"].reported_value == 1000 and facts["REV_2023"].reported_value == 900


def test_extra_prior_year_column_is_ignored_and_not_reported():
    three = statement("INCOME STATEMENTS", [row("Total revenue", ["1,000", "900", "800"], dollar=True),
                                             row("Net income", ["250", "200", "150"], dollar=True)],
                      years=("2024", "2023", "2022"), page=3)
    table = run(make_doc(three, cashflow()))
    assert {f.period.label for f in table.facts} == {"FY2024", "FY2023"}


def test_target_year_missing_from_table_is_reported_not_guessed():
    old = statement("INCOME STATEMENTS", [row("Total revenue", ["800", "700"], dollar=True),
                                          row("Net income", ["150", "100"], dollar=True)],
                    years=("2022", "2021"), page=3)
    table = run(make_doc(old, cashflow()))
    assert not [f for f in table.facts if f.metric == "revenue"]
    assert any(i.code == "not_found" and i.metric == "revenue" for i in table.issues)


# ------------------------------------------------------------------ scale


def test_thousands_scale_is_applied():
    inc = income(scale="(In thousands)")
    facts = by_id(run(make_doc(inc, cashflow())))
    assert facts["REV_2024"].scale_multiplier == 1_000
    assert facts["REV_2024"].normalized_value == Decimal(1_000_000)


def test_thousands_vs_millions_between_authoritative_tables_is_an_unresolved_conflict():
    table = run(make_doc(income(), income(scale="(In thousands)"), cashflow()))
    assert not [f for f in table.facts if f.metric == "revenue"]
    kinds = {(c.kind, c.metric) for c in table.conflicts}
    assert ("incompatible_primary_duplicates", "revenue") in kinds and all(not c.resolved for c in table.conflicts)


def test_missing_scale_blocks_the_table():
    table = run(make_doc(income(scale=None), cashflow()))
    assert not [f for f in table.facts if f.metric == "revenue"]
    assert "missing_scale_evidence" in codes(table)


def test_conflicting_scale_statements_in_one_table_are_ambiguous():
    inc = statement("INCOME STATEMENTS", [row("Total revenue", ["1,000", "900"], dollar=True)],
                    scale="(In millions) (In thousands)", page=3)
    table = run(make_doc(inc, cashflow()))
    assert "ambiguous_scale" in codes(table)


def test_inline_xbrl_scale_mismatch_is_rejected():
    ix = {"id": "ix1", "name": "us-gaap:Revenues", "scale": "3", "contextRef": "c1", "unitRef": "usd"}
    inc = income([row("Total revenue", [("1,000", ix), "900"], dollar=True), row("Net income", ["250", "200"], dollar=True)])
    table = run(make_doc(inc, cashflow()))
    assert "ix_scale_mismatch" in codes(table)
    assert "REV_2024" not in by_id(table)


def test_inline_xbrl_agreement_supplies_element_id():
    ix = {"id": "ix_rev24", "name": "us-gaap:Revenues", "scale": "6", "contextRef": "c1", "unitRef": "usd"}
    inc = income([row("Total revenue", [("1,000", ix), "900"], dollar=True), row("Net income", ["250", "200"], dollar=True)])
    fact = by_id(run(make_doc(inc, cashflow())))["REV_2024"]
    assert fact.locator.element_id == "ix_rev24"


# ------------------------------------------------------------------ signs


def test_parenthesized_negative_is_signed():
    inc = income([row("Total revenue", ["1,000", "900"], dollar=True), row("Net income", ["(45)", "200"], dollar=True)])
    cf = cashflow([row("Net income", ["(45)", "200"], dollar=True), row("Net cash from operations", ["400", "350"])])
    ni = by_id(run(make_doc(inc, cf)))["NI_2024"]
    assert ni.reported_value == -45 and ni.normalized_value == Decimal(-45_000_000) and ni.raw_amount_text.startswith("$(")


def test_inline_xbrl_sign_must_agree_with_parentheses():
    bad = {"id": "ix9", "name": "us-gaap:NetIncomeLoss", "scale": "6", "contextRef": "c1", "unitRef": "usd"}  # no sign="-"
    inc = income([row("Total revenue", ["1,000", "900"], dollar=True), row("Net income", [("(45)", bad), "200"], dollar=True)])
    assert "ix_sign_mismatch" in codes(run(make_doc(inc, cashflow())))


# ------------------------------------------------------------------ scope and basis


def test_segment_table_is_excluded_as_a_legitimate_scope_difference():
    seg = statement("INCOME STATEMENTS - BY SEGMENT", [row("Total revenue", ["300", "250"], dollar=True)], page=6)
    table = run(make_doc(income(), seg, cashflow()))
    assert table.conflicts == [] and by_id(table)["REV_2024"].reported_value == 1000
    assert any(e.reason.startswith("scope_segment") for e in table.exclusions)


def test_unidentified_table_is_excluded_not_used():
    other = statement("SEGMENT INFORMATION", [row("Total revenue", ["999", "888"], dollar=True)], page=7)
    table = run(make_doc(income(), other, cashflow()))
    assert by_id(table)["REV_2024"].reported_value == 1000
    assert [e.reason for e in table.exclusions if e.table_index == 1] == ["scope_segment_differs_from_consolidated"]


def test_table_with_no_title_or_cues_is_excluded_as_unidentified():
    other = statement("NOTE 12 - DETAILS", [row("Total revenue", ["999", "888"], dollar=True)], page=7)
    table = run(make_doc(income(), other, cashflow()))
    assert by_id(table)["REV_2024"].reported_value == 1000
    assert [e.reason for e in table.exclusions if e.table_index == 1] == ["statement_not_identified"]


def test_non_gaap_table_is_excluded():
    adj = statement("NON-GAAP INCOME STATEMENTS (ADJUSTED)", [row("Total revenue", ["1,100", "950"], dollar=True)], page=6)
    table = run(make_doc(income(), adj, cashflow()))
    assert table.conflicts == [] and by_id(table)["REV_2024"].reported_value == 1000
    assert any(e.reason.startswith("basis_non_gaap") for e in table.exclusions)


def test_missing_consolidation_evidence_blocks_all_facts():
    table = run(standard_doc(consolidation=False))
    assert table.facts == [] and "missing_scope_evidence" in codes(table)


def test_missing_gaap_evidence_blocks_all_facts():
    table = run(standard_doc(gaap=False))
    assert table.facts == [] and "missing_basis_evidence" in codes(table)


def test_missing_currency_evidence_is_reported():
    inc = income([row("Total revenue", ["1,000", "900"]), row("Net income", ["250", "200"])])
    table = run(make_doc(inc, cashflow([row("Net cash from operations", ["400", "350"])]), currency=False))
    assert "missing_currency_evidence" in codes(table) and table.facts == []


# ------------------------------------------------------------------ duplicates


def test_matching_net_income_duplicates_are_preserved_as_corroboration():
    ni = by_id(run(standard_doc()))["NI_2024"]
    assert {o.statement_kind for o in ni.corroborating_occurrences} == {"cash_flow", "comprehensive_income"}
    assert ni.conflict_status == "none"


def test_matching_duplicate_primary_statements_are_corroboration_not_conflict():
    table = run(make_doc(income(), income(page=8), cashflow()))
    assert table.conflicts == []
    rev = by_id(table)["REV_2024"]
    assert [o.statement_kind for o in rev.corroborating_occurrences] == ["income"]


def test_incompatible_primary_duplicates_emit_no_fact_and_surface_a_conflict():
    other = income([row("Total revenue", ["1,001", "900"], dollar=True), row("Net income", ["250", "200"], dollar=True)], page=8)
    table = run(make_doc(income(), other, cashflow()))
    facts = by_id(table)
    assert "REV_2024" not in facts and "REV_2023" in facts
    conflict = next(c for c in table.conflicts if c.metric == "revenue")
    assert (conflict.kind, conflict.period_label, conflict.resolved) == ("incompatible_primary_duplicates", "FY2024", False)
    assert {str(o.reported_value) for o in conflict.occurrences} == {"1000", "1001"}


def test_disagreeing_non_authoritative_occurrence_keeps_authoritative_value_and_flags_it():
    cf = cashflow([row("Net income", ["251", "200"], dollar=True), row("Net cash from operations", ["400", "350"])])
    table = run(make_doc(income(), cf))
    ni = by_id(table)["NI_2024"]
    assert ni.reported_value == 250 and ni.conflict_status == "authoritative_retained_with_disagreeing_occurrences"
    assert [c.kind for c in table.conflicts] == ["corroboration_mismatch"]


def test_metric_found_only_in_a_corroborating_statement_has_no_authoritative_fact():
    table = run(make_doc(comprehensive(), cashflow()))
    assert "NI_2024" not in by_id(table) and "no_authoritative_occurrence" in codes(table)


# ------------------------------------------------------------------ missing evidence


def test_dash_amount_is_missing_not_zero():
    inc = income([row("Total revenue", ["1,000", "—"], dollar=True), row("Net income", ["250", "200"], dollar=True)])
    table = run(make_doc(inc, cashflow()))
    assert "REV_2023" not in by_id(table) and "REV_2024" in by_id(table)
    assert any(i.code == "missing_amount" and i.period_label == "FY2023" for i in table.issues)


def test_blank_amount_cell_is_a_column_alignment_issue():
    inc = income([row("Total revenue", ["1,000", None], dollar=True), row("Net income", ["250", "200"], dollar=True)])
    table = run(make_doc(inc, cashflow()))
    assert "column_alignment" in codes(table) and not [f for f in table.facts if f.metric == "revenue"]


def test_missing_period_wording_blocks_the_table():
    inc = income(period_phrase=None)
    table = run(make_doc(inc, cashflow()))
    assert not [f for f in table.facts if f.metric == "revenue"] and "missing_period_evidence" in codes(table)


def test_quarterly_header_is_not_accepted_as_annual():
    inc = income(period_phrase="Three Months Ended June 30,")
    table = run(make_doc(inc, cashflow()))
    assert "period_not_annual" in codes(table) and not [f for f in table.facts if f.metric == "revenue"]


def test_absent_statement_is_explained_with_not_found():
    table = run(make_doc(income()))
    assert any(i.code == "not_found" and i.metric == "operating_cash_flow" for i in table.issues)


def test_output_is_deterministic():
    html = standard_doc()
    assert run(html).model_dump_json() == run(html).model_dump_json()
