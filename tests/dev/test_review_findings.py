"""Regression tests for the independent review findings R1-R8 (review/external_review/REVIEW.md).

These are exposed DEVELOPMENT tests: each reproduces a failure the reviewer demonstrated, then pins the fix.
All documents are synthetic (fictional company). Tests that need a Microsoft-shaped identity or the
frozen numbers use them only to exercise the evaluator, never as evidence about a real filing.
"""

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from finreport.cli import main
from finreport.evaluation import evaluate
from finreport.extract import extract_facts
from finreport.ingest import HttpResponse, InvalidFiling, RateLimiter, fetch_filing, urllib_transport
from finreport.manifest import initial_manifest
from finreport.parsing import parse_document, resolve_locator
from finreport.schemas import FactTable
from helpers import CHECKSUM, CURRENCY_TEXT, TARGET, cashflow, comprehensive, income, make_doc, row, standard_doc, statement

EXPECTED = json.loads((Path(__file__).parents[2] / "evaluation" / "expected_facts.json").read_text())


def run(html: bytes):
    return extract_facts(html, TARGET, CHECKSUM)


def by_id(table):
    return {f.evidence_id: f for f in table.facts}


def codes(table):
    return sorted({i.code for i in table.issues})


def six(table):
    return sorted(by_id(table)) == ["NI_2023", "NI_2024", "OCF_2023", "OCF_2024", "REV_2023", "REV_2024"]


# =============================================================== R1: header rows mixing a label and years


def doc_with_headers(**kw):
    return make_doc(income(**kw), comprehensive(**kw), cashflow(**kw))


def test_r1_period_label_and_years_in_one_row_yields_six_facts():
    table = run(doc_with_headers(header_style="label_in_year_row"))
    assert six(table) and table.issues == []
    fact = by_id(table)["REV_2024"]
    assert fact.locator.column == "2024" and fact.locator.header_row_index == 0
    assert (fact.reported_value, by_id(table)["REV_2023"].reported_value) == (1000, 900)


def test_r1_reordered_years_in_mixed_header_map_by_header_not_position():
    inc = statement("INCOME STATEMENTS", [row("Total revenue", ["900", "1,000"], dollar=True), row("Net income", ["200", "250"], dollar=True)],
                    years=("2023", "2024"), header_style="label_in_year_row", page=3)
    facts = by_id(run(make_doc(inc, cashflow())))
    assert facts["REV_2024"].reported_value == 1000 and facts["REV_2023"].reported_value == 900


def test_r1_current_year_is_not_inferred_from_a_fixed_position():
    inc = statement("INCOME STATEMENTS", [row("Total revenue", ["1,100", "1,000"], dollar=True), row("Net income", ["260", "250"], dollar=True)],
                    years=("2025", "2024"), header_style="label_in_year_row", page=3)
    table = run(make_doc(inc, cashflow()))
    assert by_id(table)["REV_2024"].reported_value == 1000  # second column, because the header says 2024
    assert "REV_2025" not in by_id(table) and "REV_2023" not in by_id(table)
    assert any(i.code == "not_found" and i.period_label == "FY2023" and i.metric == "revenue" for i in table.issues)


@pytest.mark.parametrize("label,years", [
    ("Years Ended", ("June 30, 2024", "June 30, 2023")),              # label cell + full dates in the year cells
    ("", ("Year Ended June 30, 2024", "Year Ended June 30, 2023")),   # label inside each date cell
    ("(In millions) Year Ended", ("June 30, 2024", "June 30, 2023")),
])
def test_r1_same_row_date_forms(label, years):
    inc = statement("INCOME STATEMENTS", [row("Total revenue", ["1,000", "900"], dollar=True), row("Net income", ["250", "200"], dollar=True)],
                    years=years, period_phrase=label, header_style="label_in_year_row", page=3)
    facts = by_id(run(make_doc(inc, cashflow())))
    assert (facts["REV_2024"].reported_value, facts["REV_2023"].reported_value) == (1000, 900)
    assert str(facts["REV_2024"].period.end) == "2024-06-30" and str(facts["REV_2023"].period.end) == "2023-06-30"


def test_r1_a_data_row_with_year_like_numbers_is_not_mistaken_for_a_header():
    inc = statement("INCOME STATEMENTS", [row("Total revenue", ["2024", "2023"], dollar=True)], period_phrase=None,
                    header_style="label_in_year_row", page=3)
    table = run(make_doc(inc, cashflow()))
    assert "REV_2024" not in by_id(table) and "missing_period_evidence" in codes(table)


def test_r1_quarterly_label_in_the_year_row_is_still_rejected():
    table = run(doc_with_headers(header_style="label_in_year_row", period_phrase="Three Months Ended June 30,"))
    assert table.facts == [] and "period_not_annual" in codes(table)


def test_r1_missing_period_wording_in_a_mixed_row_is_reported():
    table = run(doc_with_headers(header_style="label_in_year_row", period_phrase=None))
    assert table.facts == [] and "missing_period_evidence" in codes(table)


def test_r1_duplicate_period_columns_are_rejected():
    inc = statement("INCOME STATEMENTS", [row("Total revenue", ["1,000", "999"], dollar=True)], years=("2024", "2024"),
                    header_style="label_in_year_row", page=3)
    table = run(make_doc(inc, cashflow()))
    assert "duplicate_period_columns" in codes(table) and "REV_2024" not in by_id(table)


def test_r1_a_second_different_period_header_in_the_table_is_ambiguous():
    second = '<tr><td>Year Ended June 30,</td><td>2022</td><td></td><td>2021</td><td></td></tr>'
    inc = income(header_style="label_in_year_row", extra_header_rows=second)
    table = run(make_doc(inc, cashflow()))
    assert "ambiguous_period_header" in codes(table) and not [f for f in table.facts if f.metric == "revenue"]


def test_r1_a_repeated_identical_header_is_not_ambiguous():
    again = '<tr><td>Year Ended June 30,</td><td>2024</td><td></td><td>2023</td><td></td></tr>'
    table = run(make_doc(income(header_style="label_in_year_row", extra_header_rows=again), cashflow()))
    assert "ambiguous_period_header" not in codes(table) and "REV_2024" in by_id(table)


# =============================================================== R2: hidden text must not alter amounts

HIDDEN_FORMS = [
    '<span style="display:none">9</span>',
    '<span style="display: none;">9</span>',
    '<span style="visibility:hidden">9</span>',
    '<span style="font-size:0">9</span>',
    '<span style="font-size:0px">9</span>',
    '<span style="opacity:0">9</span>',
    '<span style="mso-hide:all">9</span>',
    '<span hidden>9</span>',
    "<ix:hidden>9</ix:hidden>",
    '<span><b><i style="display:none">9</i></b></span>',  # nested hidden element
]


def hidden_doc(inner: str) -> bytes:
    html = make_doc(income([row("Total revenue", ["1000", "900"], dollar=True), row("Net income", ["250", "200"], dollar=True)]), cashflow())
    assert b"<td>1000</td>" in html
    return html.replace(b"<td>1000</td>", f"<td>1000{inner}</td>".encode())


@pytest.mark.parametrize("inner", HIDDEN_FORMS)
def test_r2_hidden_text_in_an_amount_cell_is_ignored(inner):
    html = hidden_doc(inner)
    table = run(html)
    rev = by_id(table)["REV_2024"]
    assert six(table) and table.issues == []
    assert rev.reported_value == 1000 and rev.normalized_value == 1_000_000_000
    assert rev.raw_amount_text == "$1000"
    assert "9" not in next(e.text for e in rev.excerpts if e.kind == "amount").replace("900", "").replace("350", "")
    doc = parse_document(html)
    loc = rev.locator
    assert resolve_locator(doc, loc.table_index, loc.row_index, loc.cell_index) == "1000"


def test_r2_the_same_markup_visible_is_counted():
    assert by_id(run(hidden_doc("<span>9</span>")))["REV_2024"].reported_value == 10009  # control: visible text counts


def test_r2_visible_inline_xbrl_text_is_preserved():
    ix = {"id": "ix1", "name": "us-gaap:Revenues", "scale": "6", "contextRef": "c", "unitRef": "usd"}
    inc = income([row("Total revenue", [("1,000", ix), "900"], dollar=True), row("Net income", ["250", "200"], dollar=True)])
    fact = by_id(run(make_doc(inc, cashflow())))["REV_2024"]
    assert fact.reported_value == 1000 and fact.locator.element_id == "ix1"


def test_r2_a_hidden_cell_keeps_its_position_so_locators_stay_stable():
    html = make_doc(income(), cashflow()).replace(
        b"<tr><td>Total revenue</td>", b'<tr><td>Total revenue</td><td style="display:none">777</td>', 1)
    table = run(html)
    rev = by_id(table)["REV_2024"]
    assert six(table) and rev.reported_value == 1000
    doc = parse_document(html)
    assert resolve_locator(doc, rev.locator.table_index, rev.locator.row_index, rev.locator.cell_index) == "1,000"
    assert rev.locator.cell_index > 3  # the hidden <td> still counts in the physical index


def test_r2_a_hidden_row_is_ignored_and_does_not_create_a_conflict():
    hidden_row = '<tr style="display:none"><td>Total revenue</td><td></td><td>9,999</td><td></td><td>8,888</td></tr>'
    inc = income(extra_header_rows=hidden_row)
    table = run(make_doc(inc, cashflow()))
    assert six(table) and table.conflicts == [] and by_id(table)["REV_2024"].reported_value == 1000


def test_r2_text_outside_tables_follows_the_same_rule():
    html = standard_doc(consolidation=False).replace(
        b"</body>", b'<div style="display:none"><p>The consolidated financial statements include the accounts of Examplecorp '
                    b"and its subsidiaries.</p></div></body>")
    table = run(html)
    assert table.facts == [] and "missing_scope_evidence" in codes(table)  # hidden evidence is not evidence


def test_r2_a_hidden_statement_heading_does_not_identify_the_table():
    html = make_doc(income(title=None), cashflow())
    html = html.replace(b"<table>", b'<div style="display:none">INCOME STATEMENTS</div><table>', 1)
    assert "REV_2024" not in by_id(run(html))


def test_r2_white_text_in_an_amount_cell_is_flagged_not_trusted():
    html = make_doc(income([row("Total revenue", ["1000", "900"], dollar=True), row("Net income", ["250", "200"], dollar=True)]), cashflow())
    html = html.replace(b"<td>1000</td>", b'<td>1000<span style="color:#ffffff">9</span></td>')
    table = run(html)
    assert "REV_2024" not in by_id(table) and "ambiguous_text_visibility" in codes(table)
    assert by_id(table)["REV_2023"].reported_value == 900  # unaffected cells still extract


def test_r2_a_hidden_inline_xbrl_tag_is_not_used_for_checks():
    bad = '<span style="display:none"><ix:nonFraction id="h" scale="3" name="x">1</ix:nonFraction></span>'
    good = '<ix:nonFraction id="g" scale="6" name="us-gaap:Revenues" unitRef="usd">1,000</ix:nonFraction>'
    html = make_doc(income([row("Total revenue", ["1,000", "900"], dollar=True), row("Net income", ["250", "200"], dollar=True)]), cashflow())
    html = html.replace(b"<td>1,000</td>", f"<td>{bad}{good}</td>".encode(), 1)
    table = run(html)
    assert "ix_scale_mismatch" not in codes(table) and by_id(table)["REV_2024"].locator.element_id == "g"


# =============================================================== R3 / R4: acceptance is separate from numeric scores

MSFT = initial_manifest().target


def amounts(prefix):
    known = {f["evidence_id"]: f["reported_value"] for f in EXPECTED["facts"]}
    return [f"{known[f'{prefix}_2024']:,}", f"{known[f'{prefix}_2023']:,}"]


def numbers_doc(ni_cashflow_2024=None) -> bytes:
    ni_cf = amounts("NI") if ni_cashflow_2024 is None else [ni_cashflow_2024, amounts("NI")[1]]
    # currency=False on purpose: this base is ALSO the review's NEGATIVE input for the currency probes (no governing
    # currency evidence). Its bytes are frozen by hash in tests/dev/test_c1_c2.py. Positive cases add evidence explicitly.
    return make_doc(
        income([row("Total revenue", amounts("REV"), dollar=True), row("Net income", amounts("NI"), dollar=True)]),
        cashflow([row("Net income", ni_cf, dollar=True), row("Net cash from operations", amounts("OCF"))]),
        currency=False,
    )


def positive_numbers_doc(ni_cashflow_2024=None) -> bytes:
    """numbers_doc() plus a genuine governing-currency sentence (the C2 positive case)."""
    return numbers_doc(ni_cashflow_2024).replace(b"</body>", f"<p>{CURRENCY_TEXT}</p></body>".encode())


def verified_source(sha: str) -> dict:
    """Stand-in for ingest.verify_cached_source() output when the cache matches the manifest (C1 gate satisfied)."""
    info = {"filename": "msft-20240630.htm", "byte_count": 1000, "sha256": sha}
    return {"verified": True, "problems": [], "recorded": dict(info), "actual": dict(info)}


def ev(table, sha, **kw):
    return evaluate(EXPECTED, table, manifest_sha256=sha, source_check=verified_source(sha), **kw)


def table_dict(html=None, target=MSFT):
    html = html or positive_numbers_doc()
    out = extract_facts(html, target, hashlib.sha256(html).hexdigest())
    d = json.loads(out.model_dump_json())
    FactTable.model_validate(d)  # always schema-valid, as in the review's probes
    return d, hashlib.sha256(html).hexdigest()


def reason_codes(result):
    return [r["code"] for r in result["acceptance"]["reasons"]]


def test_r3_synthetic_microsoft_shaped_table_passes_every_machine_check_only_in_the_unit_fixture():
    table, sha = table_dict()
    result = ev(table, sha)
    assert result["numeric_fact_correctness"] == "6/6"
    assert result["acceptance"]["accepted"] is True and result["acceptance"]["reasons"] == []
    # heuristic page/section disagreement is informational only (fixture pages are 3/5, expectations say 56/59)
    assert {x["field"] for x in result["acceptance"]["informational"]["locator_heuristics_disagreeing"]} == {"printed_page"}


def test_r3_deprecated_alias_is_only_a_diagnostic_and_sits_beside_the_acceptance_decision():
    table, sha = table_dict(target=TARGET)
    result = ev(table, sha)
    assert result["complete_fact_correctness"] == result["numeric_fact_correctness"] == "6/6"
    assert result["acceptance"]["accepted"] is False and "DIAGNOSTIC ONLY" in result["numeric_fact_correctness_note"]


def test_r3_right_numbers_wrong_filing_has_full_numeric_score_but_is_rejected():
    table, sha = table_dict(target=TARGET)  # Examplecorp identity with Microsoft's expected numbers (the review's probe)
    result = ev(table, sha)
    assert result["numeric_fact_correctness"] == "6/6"  # diagnostic only
    assert result["acceptance"]["accepted"] is False
    detail = next(r["detail"] for r in result["acceptance"]["reasons"] if r["code"] == "source_identity_mismatch")
    assert {"company", "cik", "accession", "url"} <= set(detail)


def test_r3_wrong_evidence_id_and_non_gaap_basis_block_acceptance_but_not_the_numeric_diagnostic():
    table, sha = table_dict()
    table["facts"][0].update(evidence_id="WRONG_EVIDENCE_ID", accounting_basis="non-GAAP")
    FactTable.model_validate(table)
    result = ev(table, sha)
    assert result["numeric_fact_correctness"] == "6/6"
    failed = next(r["detail"] for r in result["acceptance"]["reasons"] if r["code"] == "fact_checks_failed")
    assert set(failed["REV_2024"]) >= {"evidence_id", "accounting_basis"}


@pytest.mark.parametrize("sha_arg,code", [(None, "source_checksum_unverified"), ("00" * 32, "source_checksum_mismatch")])
def test_r3_table_checksum_must_equal_the_manifest_checksum(sha_arg, code):
    table, _ = table_dict()
    assert code in reason_codes(evaluate(EXPECTED, table, manifest_sha256=sha_arg, source_check=verified_source(table['source']['checksum'])))


def test_r3_fact_and_locator_checksums_must_match_the_table_checksum():
    table, sha = table_dict()
    other = "ef" * 32
    table["facts"][1]["source_checksum"] = other
    table["facts"][1]["locator"]["source_checksum"] = other  # internally consistent, so still schema-valid
    FactTable.model_validate(table)
    result = ev(table, sha)
    failed = next(r["detail"] for r in result["acceptance"]["reasons"] if r["code"] == "fact_checks_failed")
    assert set(failed["REV_2023"]) >= {"fact_checksum_equals_table", "locator_checksum_equals_table"}


def test_r3_schema_invalid_tables_are_rejected():
    table, sha = table_dict()
    del table["facts"][0]["excerpts"]
    assert "schema_invalid" in reason_codes(ev(table, sha))


def test_r3_fact_source_url_and_accession_must_match():
    table, sha = table_dict()
    table["facts"][0]["source_url"] = "https://example.invalid/other.htm"
    table["facts"][0]["accession"] = "0001234567-24-000001"
    failed = next(r["detail"] for r in ev(table, sha)["acceptance"]["reasons"] if r["code"] == "fact_checks_failed")
    assert set(failed["REV_2024"]) >= {"fact_source_url", "fact_accession"}


def test_r3_locator_statement_row_or_column_disagreement_blocks():
    table, sha = table_dict()
    table["facts"][2]["locator"]["row"] = "Some other row"
    failed = next(r["detail"] for r in ev(table, sha)["acceptance"]["reasons"] if r["code"] == "fact_checks_failed")
    assert "locator_row" in failed["NI_2024"]


def test_r3_duplicate_and_missing_facts_are_rejected():
    table, sha = table_dict()
    table["facts"].append(dict(table["facts"][0]))
    assert {"unexpected_extra_facts", "fact_checks_failed"} <= set(reason_codes(ev(table, sha)))
    table, sha = table_dict()
    table["facts"].pop(0)
    assert "expected_fact_missing" in reason_codes(ev(table, sha))


def test_r4_unresolved_conflict_blocks_acceptance_even_with_a_full_numeric_score():
    table, sha = table_dict(positive_numbers_doc(ni_cashflow_2024=f"{amounts('NI')[0].replace(',', '')[:-1]}9"))
    assert [c["kind"] for c in table["conflicts"]] == ["corroboration_mismatch"]
    result = ev(table, sha)
    assert result["numeric_fact_correctness"] == "6/6" and result["unresolved_conflicts"] == 1
    assert result["acceptance"]["accepted"] is False
    assert {"unresolved_conflicts", "fact_checks_failed"} <= set(reason_codes(result))


def test_r4_only_unresolved_conflicts_are_counted_as_unresolved():
    table, sha = table_dict()
    table["conflicts"].append({"kind": "corroboration_mismatch", "metric": "net_income", "period_label": "FY2024", "scope": "consolidated",
                               "accounting_basis": "US GAAP", "resolved": True, "decision": "reviewed", "occurrences": []})
    result = ev(table, sha)
    assert (result["unresolved_conflicts"], result["resolved_conflicts"]) == (0, 1)
    assert "unresolved_conflicts" not in reason_codes(result)


def test_r4_blocking_vs_non_blocking_issue_policy():
    # A defect in a corroborating-only statement (net income in the cash-flow statement) is informational ...
    cf = cashflow([row("Net income", ["—", amounts("NI")[1]], dollar=True), row("Net cash from operations", amounts("OCF"))])
    html = make_doc(income([row("Total revenue", amounts("REV"), dollar=True), row("Net income", amounts("NI"), dollar=True)]), cf)
    table, sha = table_dict(html)
    non_blocking = [i for i in table["issues"] if not i["blocking"]]
    assert [i["code"] for i in non_blocking] == ["missing_amount"] and non_blocking[0]["statement_kind"] == "cash_flow"
    result = ev(table, sha)
    assert result["acceptance"]["accepted"] is True and result["blocking_issues"] == 0
    assert result["acceptance"]["informational"]["non_blocking_issues"]
    # ... but a defect in an authoritative statement blocks even though all six facts were produced elsewhere
    dup = income([row("Total revenue", amounts("REV"), dollar=True)], scale=None, page=9)
    table, sha = table_dict(make_doc(income([row("Total revenue", amounts("REV"), dollar=True), row("Net income", amounts("NI"), dollar=True)]),
                                      dup, cashflow([row("Net income", amounts("NI"), dollar=True), row("Net cash from operations", amounts("OCF"))])))
    assert len(table["facts"]) == 6
    result = ev(table, sha)
    assert result["acceptance"]["accepted"] is False and "blocking_extraction_issues" in reason_codes(result)


def test_r4_a_non_blocking_issue_does_not_hide_a_missing_authoritative_fact():
    cf = cashflow([row("Net income", ["—", "200"], dollar=True), row("Net cash from operations", ["400", "350"])])
    table = run(make_doc(statement("INCOME STATEMENTS", [row("Total revenue", ["1,000", "900"], dollar=True)], page=3), cf))
    assert "NI_2024" not in by_id(table)
    assert any(i.code == "not_found" and i.metric == "net_income" and i.period_label == "FY2024" for i in table.issues)


def synthetic_microsoft_shaped_root(tmp_path, html, governing_currency=True):
    """Root whose manifest targets the real filing identity but whose cached file is a SYNTHETIC document (unit fixture)."""
    assert main(["--root", str(tmp_path), "init"]) == 0
    marker = b"<p>Microsoft Corporation FORM 10-K for the fiscal year ended June 30, 2024 (synthetic unit-test document)</p>"
    # positive wrapper: this root is the C1 acceptance control, so the cached document carries genuine governing currency
    # evidence; the negative currency probes call extract_facts() directly on numbers_doc() and never go through here.
    governing = f"<p>{CURRENCY_TEXT}</p>".encode() if governing_currency else b""
    src = tmp_path / "elsewhere.htm"
    src.write_bytes(marker + governing + html)
    assert main(["--root", str(tmp_path), "import-file", str(src), "--min-bytes", "100",
                 "--method", "synthetic unit-test document written by the test, not a filing"]) == 0
    (tmp_path / "evaluation").mkdir(exist_ok=True)
    (tmp_path / "evaluation" / "expected_facts.json").write_text(json.dumps(EXPECTED))


def test_r4_evaluate_cli_exit_status_follows_acceptance_not_the_numeric_score(tmp_path, capsys):
    synthetic_microsoft_shaped_root(tmp_path, numbers_doc())
    assert main(["--root", str(tmp_path), "extract"]) == 0
    assert main(["--root", str(tmp_path), "evaluate"]) == 0
    out = capsys.readouterr().out
    assert "numeric diagnostic (NOT acceptance): 6/6" in out and "ACCEPTED_BY_MACHINE_CHECKS" in out

    facts_path = tmp_path / "output" / "fact_table.json"
    d = json.loads(facts_path.read_text())
    d["source"]["checksum"] = "ab" * 32  # table no longer matches the manifest's cached file
    facts_path.write_text(json.dumps(d))
    assert main(["--root", str(tmp_path), "evaluate"]) == 1
    assert "REJECTED" in capsys.readouterr().out


def test_r4_evaluate_cli_exits_nonzero_on_unresolved_conflict(tmp_path):
    synthetic_microsoft_shaped_root(tmp_path, numbers_doc(ni_cashflow_2024="88,137"))
    assert main(["--root", str(tmp_path), "extract"]) == 6  # extract already signals the conflict
    assert main(["--root", str(tmp_path), "evaluate"]) == 1
    report = json.loads((tmp_path / "output" / "evaluation_report.json").read_text())
    assert report["numeric_fact_correctness"] == "6/6" and report["unresolved_conflicts"] == 1
    assert report["acceptance"]["accepted"] is False


def test_r4_evaluator_never_imports_the_extractor():
    import re
    src = (Path(__file__).parents[2] / "src" / "finreport" / "evaluation.py").read_text()
    assert not re.search(r"^\s*(?:from|import)\s+finreport\.(?:extract|parsing|ingest)", src, re.M)


# =============================================================== R5: currency must be evidenced, contradictions rejected


def with_scale(text: str) -> bytes:
    return standard_doc().replace(b"(In millions, except per share amounts)", text.encode())


def test_r5_explicit_canadian_dollars_are_not_labelled_usd():
    table = run(with_scale("(In millions of Canadian dollars, except per share amounts)"))
    assert table.facts == [] and "currency_contradiction" in codes(table)
    # the authoritative (income) statement's contradiction blocks; the corroborating cash-flow one is informational
    assert any(i.blocking and i.statement_kind == "income" for i in table.issues if i.code == "currency_contradiction")


@pytest.mark.parametrize("text", ["(In millions, CAD)", "(In millions of euros)", "(In millions of pounds sterling)", "(In millions, JPY)",
                                  "(In millions of \u20ac)", "(In \u00a3 millions)", "(In millions of C$)", "(In millions of Swiss francs)"])
def test_r5_other_currency_words_in_the_statement_header_are_rejected(text):
    assert run(with_scale(text)).facts == []


def test_r5_a_non_usd_heading_is_rejected_too():
    html = standard_doc().replace(b"INCOME STATEMENTS", b"INCOME STATEMENTS (CANADIAN DOLLARS)")
    table = run(html)
    assert not [f for f in table.facts if f.metric == "revenue"] and "currency_contradiction" in codes(table)


def test_r5_a_document_level_reporting_currency_statement_contradicts_the_dollar_sign():
    html = make_doc(income(), comprehensive(), cashflow()).replace(
        b"</body>", b"<p>The financial statements are presented in Canadian dollars.</p></body>")
    table = run(html)
    assert table.facts == [] and "currency_contradiction" in codes(table)


def test_r5_a_subsidiary_functional_currency_does_not_block_a_usd_filing():
    html = standard_doc().replace(
        b"</body>", b"<p>The functional currency of our Canadian subsidiary is the Canadian dollar.</p></body>")
    assert six(run(html))


def test_r5_evidence_kind_is_recorded():
    # (b) the default synthetic document carries a governing sentence; (a) a USD statement in the scale line wins when present
    assert {f.currency_evidence_kind for f in run(standard_doc()).facts} == {"document_statement_usd"}
    explicit = run(with_scale("(In millions of U.S. dollars, except per share amounts)"))
    assert six(explicit) and {f.currency_evidence_kind for f in explicit.facts} == {"statement_header_usd"}


def test_r5_a_passing_mention_of_the_dollar_is_not_a_currency_statement():
    html = make_doc(income([row("Total revenue", ["1,000", "900"]), row("Net income", ["250", "200"])]),
                    cashflow([row("Net cash from operations", ["400", "350"])]), currency=False)
    html = html.replace(b"</body>", b"<p>A stronger U.S. dollar reduced revenue.</p></body>")
    table = run(html)
    assert table.facts == [] and "missing_currency_evidence" in codes(table)


def test_r5_mixed_usd_and_other_currency_text_is_rejected_conservatively():
    assert run(with_scale("(In millions of U.S. dollars; Canadian dollar amounts translated)")).facts == []


# =============================================================== R6: parent-only / standalone scope


def retitled(title: str) -> bytes:
    return standard_doc().replace(b"INCOME STATEMENTS", title.encode(), 1)


@pytest.mark.parametrize("title", [
    "PARENT COMPANY INCOME STATEMENTS",
    "PARENT-ONLY INCOME STATEMENTS",
    "STANDALONE INCOME STATEMENTS",
    "UNCONSOLIDATED INCOME STATEMENTS",
    "CONSOLIDATING INCOME STATEMENTS",
    "INCOME STATEMENTS OF THE REGISTRANT",
    "CONDENSED INCOME STATEMENTS - COMPANY ONLY",
])
def test_r6_non_consolidated_income_statements_are_not_emitted_as_consolidated(title):
    table = run(retitled(title))
    assert not [f for f in table.facts if f.metric == "revenue"]
    assert any(e.reason == "scope_non_consolidated_entity_differs_from_consolidated" for e in table.exclusions)
    assert not [f for f in table.facts if f.scope != "consolidated"]


def test_r6_the_reviewers_probe_no_income_statement_facts_inherit_the_document_default():
    table = run(retitled("PARENT COMPANY INCOME STATEMENTS"))
    assert "REV_2024" not in by_id(table) and "NI_2024" not in by_id(table)
    assert any(i.code == "no_authoritative_occurrence" and i.metric == "net_income" for i in table.issues)  # NI seen only in corroborating tables
    assert any(i.code == "not_found" and i.metric == "revenue" for i in table.issues)


@pytest.mark.parametrize("title", ["INCOME STATEMENTS", "INCOME STATEMENTS (REGISTERED PUBLIC ACCOUNTING FIRM REPORT REFERENCED)",
                                   "CONSOLIDATED INCOME STATEMENTS - PARENTHETICAL AMOUNTS"])
def test_r6_ordinary_titles_are_not_mistaken_for_parent_only_statements(title):
    # guards the cue list against over-matching ("registered", "parenthetical" must not trigger)
    table = run(retitled(title))
    assert "REV_2024" in by_id(table)


def test_r6_a_parent_table_beside_the_consolidated_one_is_a_legitimate_scope_difference_not_a_conflict():
    parent = statement("PARENT COMPANY INCOME STATEMENTS", [row("Total revenue", ["50", "40"], dollar=True)], page=8)
    table = run(make_doc(income(), parent, cashflow()))
    assert table.conflicts == [] and by_id(table)["REV_2024"].reported_value == 1000
    assert any(e.reason.startswith("scope_non_consolidated") for e in table.exclusions)


def test_r6_the_cue_may_sit_in_the_tables_own_header_rows():
    inc = statement(None, [row("Total revenue", ["1,000", "900"], dollar=True), row("Net income", ["250", "200"], dollar=True)], page=3,
                    extra_header_rows="")
    inc = inc.replace("<table>", "<table><tr><td>INCOME STATEMENTS - PARENT ONLY</td></tr>", 1)
    assert not [f for f in run(make_doc(inc, cashflow())).facts if f.metric == "revenue"]


# =============================================================== R7: redirects are never followed


@pytest.fixture()
def local_server(monkeypatch):
    for var in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NO_PROXY", "*")
    hits: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            if self.path == "/start":
                self.send_response(302)
                self.send_header("Location", "/destination")
                self.end_headers()
            else:
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<html>ok</html>")

        def log_message(self, *_a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", hits
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.loopback
def test_r7_the_real_transport_issues_exactly_one_request_for_a_redirect(local_server):
    base, hits = local_server
    resp = urllib_transport(f"{base}/start", {"User-Agent": "local-test"}, 3)
    assert hits == ["/start"]  # the destination was never requested
    assert resp.status == 302 and resp.headers["location"] == "/destination"


@pytest.mark.loopback
def test_r7_a_normal_response_still_works(local_server):
    base, hits = local_server
    resp = urllib_transport(f"{base}/ok", {"User-Agent": "local-test"}, 3)
    assert (resp.status, resp.body, hits) == (200, b"<html>ok</html>", ["/ok"])


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_r7_fetch_refuses_a_redirect_without_a_second_request(tmp_path, status):
    calls = []

    def transport(url, headers, timeout):
        calls.append(url)
        return HttpResponse(status, {"location": "https://www.sec.gov/elsewhere.htm"}, b"", url)

    m = initial_manifest()
    with pytest.raises(InvalidFiling, match="redirect"):
        fetch_filing(m, tmp_path, env={"SEC_USER_AGENT": "unit-test-agent"}, transport=transport,
                     limiter=RateLimiter(0, sleep=lambda s: None), sleep=lambda s: None)
    assert len(calls) == 1 and not list(tmp_path.rglob("*.htm"))


# =============================================================== R8: malformed inline-XBRL metadata


def ix_doc(**attrs) -> bytes:
    ix = {"name": "us-gaap:Revenues", "contextRef": "c", "unitRef": "usd", **attrs}
    return make_doc(income([row("Total revenue", [("1,000", ix), "900"], dollar=True), row("Net income", ["250", "200"], dollar=True)]), cashflow())


def test_r8_the_reviewers_probe_a_non_numeric_scale_is_a_structured_issue_not_a_crash():
    table = run(ix_doc(scale="not-a-number"))
    issue = next(i for i in table.issues if i.code == "ix_scale_invalid")
    assert issue.metric == "revenue" and issue.period_label == "FY2024" and issue.blocking
    assert "REV_2024" not in by_id(table) and by_id(table)["REV_2023"].reported_value == 900  # no value invented


@pytest.mark.parametrize("scale", ["", "   ", "3.5", "1e3", "0x6", "٦", "six", "-3", "13", "25", "999999999999999999999999", "+6x", "6 6"])
def test_r8_malformed_or_unreasonable_scales_are_rejected_without_exceptions(scale):
    table = run(ix_doc(scale=scale))
    assert any(i.code == "ix_scale_invalid" for i in table.issues) and "REV_2024" not in by_id(table)


@pytest.mark.parametrize("scale", ["6", "+6", " 6 ", "06"])
def test_r8_valid_scale_syntax_still_extracts(scale):
    assert by_id(run(ix_doc(scale=scale)))["REV_2024"].reported_value == 1000


def test_r8_a_valid_but_wrong_scale_is_still_a_mismatch_not_invalid():
    assert "ix_scale_mismatch" in codes(run(ix_doc(scale="3")))


def test_r8_an_invalid_sign_attribute_is_a_structured_issue():
    table = run(ix_doc(scale="6", sign="+"))
    assert "ix_sign_invalid" in codes(table) and "REV_2024" not in by_id(table)


def test_r8_valid_sign_and_scale_checks_are_preserved():
    ix = {"name": "us-gaap:NetIncomeLoss", "contextRef": "c", "unitRef": "usd", "scale": "6", "sign": "-"}
    inc = income([row("Total revenue", ["1,000", "900"], dollar=True), row("Net income", [("(45)", ix), "200"], dollar=True)])
    cf = cashflow([row("Net income", ["(45)", "200"], dollar=True), row("Net cash from operations", ["400", "350"])])
    assert by_id(run(make_doc(inc, cf)))["NI_2024"].reported_value == -45


# =============================================================== evaluator robustness (same class as R8: no crashes on bad input)


@pytest.mark.parametrize("damage", [
    lambda t: t["facts"][0].pop("period"),
    lambda t: t["facts"][0].pop("locator"),
    lambda t: t.pop("facts"),
    lambda t: t["facts"][0].__setitem__("reported_value", None),
])
def test_evaluator_rejects_malformed_tables_instead_of_crashing(damage):
    table, sha = table_dict()
    damage(table)
    result = ev(table, sha)
    assert result["acceptance"]["accepted"] is False


def test_evaluate_cli_reports_unreadable_json_with_exit_2(tmp_path):
    synthetic_microsoft_shaped_root(tmp_path, numbers_doc())
    (tmp_path / "output").mkdir(exist_ok=True)
    (tmp_path / "output" / "fact_table.json").write_text("{not json")
    assert main(["--root", str(tmp_path), "evaluate"]) == 2
