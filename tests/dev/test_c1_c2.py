"""Regression tests for review findings C1 (source integrity before acceptance) and C2 (reporting-currency evidence).

Exposed DEVELOPMENT tests (the fixes were written against them), not held-out evidence. All documents are synthetic.
The three NEGATIVE input documents of the review's probes are frozen by hash below: they must keep lacking governing
currency evidence, otherwise the probes would stop testing what they test.
"""

import hashlib
import json
from pathlib import Path

import pytest

from finreport.cli import main
from finreport.evaluation import evaluate
from finreport.extract import extract_facts
from finreport import ingest
from finreport.ingest import HttpResponse, RateLimiter, cache_path, fetch_filing, sha256_file
from finreport.manifest import initial_manifest, load_manifest, save_manifest
from finreport.schemas import FactTable
from helpers import (CHECKSUM, CURRENCY_TEXT, IX_NAMESPACES, TARGET, cashflow, income, ix_header, ixrow, make_doc, row, standard_doc,
                     unit)
from test_review_findings import EXPECTED, numbers_doc, synthetic_microsoft_shaped_root

MSFT = initial_manifest().target
INPUTS = Path(__file__).parents[2] / "review" / "c1c2" / "inputs"

# sha256 of the review probes' negative inputs, generated from the rev2 helpers BEFORE any C1/C2 change
FROZEN_NEGATIVE_HASHES = {
    "numbers_doc": "2a9bb225c9bb83fa",
    "unrelated_usd": "0367db03026adbb6",
    "debt_usd": "21554caae0b54ea8",
}


def run(html: bytes):
    return extract_facts(html, TARGET, CHECKSUM)


def by_id(table):
    return {f.evidence_id: f for f in table.facts}


def codes(table):
    return sorted({i.code for i in table.issues})


def six(table):
    return sorted(by_id(table)) == ["NI_2023", "NI_2024", "OCF_2023", "OCF_2024", "REV_2023", "REV_2024"]


def kinds(table):
    return {f.currency_evidence_kind for f in table.facts}


def with_text(html: bytes, sentence: str) -> bytes:
    return html.replace(b"</body>", f"<p>{sentence}</p></body>".encode())


def no_currency_doc(**kw) -> bytes:
    return make_doc(income(), cashflow(), currency=False, **kw)


# =============================================================== frozen negative inputs


def test_the_probes_negative_inputs_are_unchanged_and_lack_governing_currency_evidence():
    body = numbers_doc()
    docs = {
        "numbers_doc": body,
        "unrelated_usd": body.replace(b"</body>", b"<p>The USD strengthened against the euro.</p></body>"),
        "debt_usd": body.replace(b"</body>", b"<p>Our debt is denominated in U.S. dollars.</p></body>"),
    }
    for name, expected_prefix in FROZEN_NEGATIVE_HASHES.items():
        assert hashlib.sha256(docs[name]).hexdigest().startswith(expected_prefix), f"{name} bytes drifted"
    assert b"presented in U.S. dollars" not in body and CURRENCY_TEXT.encode() not in body
    # the saved copies shipped for the reviewer are the same bytes
    saved = json.loads((INPUTS / "original_input_hashes.json").read_text())
    assert hashlib.sha256(body).hexdigest() == saved["numbers_doc_no_governing_currency.html"]["sha256"]
    assert hashlib.sha256(docs["unrelated_usd"]).hexdigest() == saved["negative_unrelated_usd_mention.html"]["sha256"]
    assert hashlib.sha256(docs["debt_usd"]).hexdigest() == saved["negative_debt_denominated_usd.html"]["sha256"]


# =============================================================== C1: current source integrity before acceptance


def prepared_root(tmp_path: Path) -> Path:
    synthetic_microsoft_shaped_root(tmp_path, numbers_doc())  # positive control: governing currency evidence is added by the wrapper
    assert main(["--root", str(tmp_path), "extract"]) == 0
    return tmp_path


def evaluation_report(root: Path) -> dict:
    return json.loads((root / "output" / "evaluation_report.json").read_text())


def cached(root: Path) -> Path:
    return cache_path(root, load_manifest(root / "data" / "source_manifest.json").target)


def problems(report) -> list[str]:
    return report["source_verification"]["problems"]


def test_c1_control_unchanged_cache_is_accepted_and_the_verification_is_recorded(tmp_path):
    root = prepared_root(tmp_path)
    assert main(["--root", str(root), "evaluate"]) == 0
    report = evaluation_report(root)
    assert report["acceptance"]["accepted"] is True
    sv = report["source_verification"]
    assert sv["verified"] is True and sv["problems"] == []
    assert sv["actual"]["sha256"] == sv["recorded"]["sha256"] == sha256_file(cached(root))
    assert sv["actual"]["byte_count"] == sv["recorded"]["byte_count"] == cached(root).stat().st_size


def test_c1_changed_bytes_of_a_different_size_block_acceptance_and_exit_nonzero(tmp_path):
    root = prepared_root(tmp_path)
    cached(root).write_bytes(b"changed cached filing")
    assert main(["--root", str(root), "extract"]) == 5
    assert main(["--root", str(root), "evaluate"]) == 1
    report = evaluation_report(root)
    assert report["acceptance"]["accepted"] is False
    assert {"sha256_mismatch", "byte_count_mismatch"} <= set(problems(report))
    assert "source_verification_failed" in [r["code"] for r in report["acceptance"]["reasons"]]
    assert report["numeric_fact_correctness"] == "6/6"  # the diagnostic remains available, separately


def test_c1_a_same_size_altered_file_is_caught_by_the_checksum(tmp_path):
    root = prepared_root(tmp_path)
    data = bytearray(cached(root).read_bytes())
    data[len(data) // 2] ^= 0x01  # one flipped bit, identical length
    cached(root).write_bytes(bytes(data))
    assert cached(root).stat().st_size == load_manifest(root / "data" / "source_manifest.json").external_import.byte_count
    assert main(["--root", str(root), "evaluate"]) == 1
    report = evaluation_report(root)
    assert report["acceptance"]["accepted"] is False and problems(report) == ["sha256_mismatch"]
    assert report["numeric_fact_correctness"] == "6/6"


def test_c1_a_deleted_cached_file_blocks_acceptance(tmp_path):
    root = prepared_root(tmp_path)
    cached(root).unlink()
    assert main(["--root", str(root), "extract"]) == 2
    assert main(["--root", str(root), "evaluate"]) == 1
    report = evaluation_report(root)
    assert report["acceptance"]["accepted"] is False and problems(report) == ["cached_file_missing"]
    assert report["source_verification"]["actual"] is None
    assert report["numeric_fact_correctness"] == "6/6"


def test_c1_a_directory_in_place_of_the_cached_file_is_not_a_source(tmp_path):
    root = prepared_root(tmp_path)
    path = cached(root)
    path.unlink()
    path.mkdir()
    assert main(["--root", str(root), "evaluate"]) == 1
    assert "cached_file_missing" in problems(evaluation_report(root))


def edit_manifest(root: Path, **external_changes):
    path = root / "data" / "source_manifest.json"
    d = json.loads(path.read_text())
    d["external_import"].update(external_changes)
    path.write_text(json.dumps(d, indent=2))


def test_c1_the_recorded_filename_must_match_the_cached_file(tmp_path):
    root = prepared_root(tmp_path)
    edit_manifest(root, cached_filename="some-other-name.htm")
    assert main(["--root", str(root), "evaluate"]) == 1
    assert "filename_mismatch" in problems(evaluation_report(root))
    assert main(["--root", str(root), "extract"]) == 5


def test_c1_the_recorded_byte_count_must_match_the_cached_file(tmp_path):
    root = prepared_root(tmp_path)
    edit_manifest(root, byte_count=load_manifest(root / "data" / "source_manifest.json").external_import.byte_count + 1)
    assert main(["--root", str(root), "evaluate"]) == 1
    assert problems(evaluation_report(root)) == ["byte_count_mismatch"]


def test_c1_a_manifest_checksum_edited_to_disagree_with_the_file_blocks_acceptance(tmp_path):
    root = prepared_root(tmp_path)
    edit_manifest(root, sha256="ab" * 32)
    assert main(["--root", str(root), "evaluate"]) == 1
    assert "sha256_mismatch" in problems(evaluation_report(root))


def test_c1_the_fact_table_checksum_is_compared_with_the_verified_file(tmp_path):
    root = prepared_root(tmp_path)  # file and manifest agree; only the fact table is stale
    path = root / "output" / "fact_table.json"
    d = json.loads(path.read_text())
    stale = "cd" * 32
    d["source"]["checksum"] = stale
    for f in d["facts"]:
        f["source_checksum"] = stale
        f["locator"]["source_checksum"] = stale
    path.write_text(json.dumps(d))
    assert main(["--root", str(root), "evaluate"]) == 1
    report = evaluation_report(root)
    assert report["source_verification"]["verified"] is True  # the cache itself is fine
    assert "source_checksum_mismatch" in [r["code"] for r in report["acceptance"]["reasons"]]


def test_c1_a_pending_manifest_has_no_source_to_verify(tmp_path):
    assert main(["--root", str(tmp_path), "init"]) == 0
    sv = ingest.verify_cached_source(tmp_path, load_manifest(tmp_path / "data" / "source_manifest.json"))
    assert sv["verified"] is False and sv["problems"] == ["no_recorded_source"]


def test_c1_the_api_cannot_accept_without_a_source_verification():
    html = numbers_doc().replace(b"</body>", f"<p>{CURRENCY_TEXT}</p></body>".encode())
    sha = hashlib.sha256(html).hexdigest()
    table = json.loads(extract_facts(html, MSFT, sha).model_dump_json())
    result = evaluate(EXPECTED, table, manifest_sha256=sha)  # no source_check supplied
    assert result["numeric_fact_correctness"] == "6/6"
    assert result["acceptance"]["accepted"] is False
    assert "source_unverified" in [r["code"] for r in result["acceptance"]["reasons"]]


def test_c1_the_verified_file_checksum_is_compared_with_the_fact_table_even_without_a_manifest_checksum():
    html = standard_doc()
    real = hashlib.sha256(html).hexdigest()
    table = json.loads(extract_facts(html, MSFT, "ab" * 32).model_dump_json())  # fact table built against some other checksum
    info = {"filename": "f", "byte_count": len(html), "sha256": real}
    check = {"verified": True, "problems": [], "recorded": dict(info), "actual": dict(info)}
    result = evaluate(EXPECTED, table, manifest_sha256=None, source_check=check)
    mismatches = [r for r in result["acceptance"]["reasons"] if r["code"] == "source_checksum_mismatch"]
    assert mismatches and mismatches[0]["detail"] == {"table": "ab" * 32, "verified_file": real}
    assert result["acceptance"]["accepted"] is False


def test_c1_a_source_check_that_disagrees_with_the_supplied_manifest_checksum_is_rejected():
    html = standard_doc()
    sha = hashlib.sha256(html).hexdigest()
    table = json.loads(extract_facts(html, MSFT, sha).model_dump_json())
    check = {"verified": True, "problems": [], "recorded": {"filename": "x", "byte_count": 1, "sha256": "ee" * 32},
             "actual": {"filename": "x", "byte_count": 1, "sha256": "ee" * 32}}
    result = evaluate(EXPECTED, table, manifest_sha256=sha, source_check=check)
    assert "manifest_checksum_inconsistent" in [r["code"] for r in result["acceptance"]["reasons"]]


def test_c1_verification_also_covers_a_downloaded_retrieval_record(tmp_path):
    body = b"<html><body>Examplecorp FORM 10-K fiscal year ended June 30, 2024 " + b"x" * 500 + b"</body></html>"
    m = initial_manifest()
    m = m.model_copy(update={"target": m.target.model_copy(update={"company": "Examplecorp"})})
    resp = HttpResponse(200, {"content-type": "text/html"}, body, m.target.url)
    m = fetch_filing(m, tmp_path, env={"SEC_USER_AGENT": "unit-test-agent"}, transport=lambda *a: resp, min_bytes=100,
                     limiter=RateLimiter(0, sleep=lambda s: None), sleep=lambda s: None)
    assert m.status == "downloaded" and ingest.verify_cached_source(tmp_path, m)["verified"] is True
    cache_path(tmp_path, m.target).write_bytes(body[:-1] + b"Z")  # same size, different byte
    sv = ingest.verify_cached_source(tmp_path, m)
    assert sv["verified"] is False and sv["problems"] == ["sha256_mismatch"]


# =============================================================== C2: governing reporting-currency evidence


def test_c2_the_reviews_unrelated_usd_mention_is_not_currency_evidence():
    html = with_text(numbers_doc(), "The USD strengthened against the euro.")
    table = run(html)
    assert table.facts == [] and "missing_currency_evidence" in codes(table)
    assert not any(e.kind == "currency" for f in table.facts for e in f.excerpts)


def test_c2_the_reviews_debt_denomination_is_not_currency_evidence():
    table = run(with_text(numbers_doc(), "Our debt is denominated in U.S. dollars."))
    assert table.facts == [] and "missing_currency_evidence" in codes(table)


def test_c2_the_negative_base_alone_yields_no_facts():
    table = run(numbers_doc())
    assert table.facts == [] and "missing_currency_evidence" in codes(table)
    assert all(i.blocking for i in table.issues if i.code == "missing_currency_evidence" and i.statement_kind == "income")


@pytest.mark.parametrize("sentence", [
    "The USD strengthened against the euro.",
    "A stronger U.S. dollar reduced revenue by $2.0 billion.",
    "Revenue growth was 5% in constant U.S. dollars.",
    "Our senior notes are denominated in U.S. dollars.",
    "Our debt is denominated in USD.",
    "The functional currency of our foreign subsidiaries is the U.S. dollar.",
    "The functional currency of the Canadian subsidiary is the U.S. dollar.",
    "We hedge euro exposure against the U.S. dollar using forward contracts.",
    "Foreign exchange gains arise when the U.S. dollar weakens.",
    "Our credit facility is denominated in U.S. dollars and our financial statements are described in Note 1.",
    "Prices are quoted in U.S. dollars.",
    "Customers may pay in U.S. dollars.",
    "Our results are reported in segments.",
])
def test_c2_commentary_debt_and_functional_currency_do_not_govern_the_statements(sentence):
    table = run(with_text(numbers_doc(), sentence))
    assert table.facts == [] and "missing_currency_evidence" in codes(table)


def test_c2_dollar_symbols_alone_do_not_establish_reporting_currency():
    table = run(no_currency_doc())
    assert table.facts == [] and "missing_currency_evidence" in codes(table)


# ---- (a) statement header / scale line

HEADER_FORMS = [
    "(In millions of U.S. dollars, except per share amounts)",
    "(U.S. dollars in millions)",
    "(USD in millions)",
    "(In millions, USD)",
    "(In millions, U.S. dollars)",
    "(In millions of US$)",
    "(In thousands of USD)",
    "(Amounts in millions of United States dollars)",
]


@pytest.mark.parametrize("scale", HEADER_FORMS)
def test_c2a_an_explicit_usd_statement_in_the_scale_line_is_accepted(scale):
    thousands = "thousands" in scale
    html = make_doc(income(scale=scale), cashflow(scale=scale), currency=False)
    table = run(html)
    assert six(table) and kinds(table) == {"statement_header_usd"}
    fact = by_id(table)["REV_2024"]
    assert fact.scale_multiplier == (1_000 if thousands else 1_000_000)
    excerpt = next(e for e in fact.excerpts if e.kind == "currency")
    assert scale in excerpt.text and excerpt.where.startswith("table ") or excerpt.where.startswith("text block")


def test_c2a_a_usd_heading_above_the_statement_is_accepted():
    inc = income(scale="(In millions)").replace("<table>", "<div>Expressed in U.S. dollars</div><table>", 1)
    cf = cashflow(scale="(In millions)").replace("<table>", "<div>Expressed in U.S. dollars</div><table>", 1)
    table = run(make_doc(inc, cf, currency=False))
    assert six(table) and kinds(table) == {"statement_header_usd"}


def test_c2a_header_evidence_binds_to_its_own_table_only():
    inc = income(scale="(In millions of U.S. dollars)")
    cf = cashflow(scale="(In millions)")  # no currency wording and no document statement
    table = run(make_doc(inc, cf, currency=False))
    assert {"REV_2024", "REV_2023", "NI_2024", "NI_2023"} <= set(by_id(table))
    assert "OCF_2024" not in by_id(table) and "OCF_2023" not in by_id(table)
    assert any(i.code == "missing_currency_evidence" and i.metric == "operating_cash_flow" for i in table.issues)


def test_c2a_a_commentary_sentence_beside_a_scale_line_is_not_header_evidence():
    scale = "(The USD strengthened; amounts in millions)"
    table = run(make_doc(income(scale=scale), cashflow(scale=scale), currency=False))
    assert table.facts == [] and "missing_currency_evidence" in codes(table)


# ---- (b) document statement governing the statements / establishing reporting currency

GOVERNING_SENTENCES = [
    "The consolidated financial statements are presented in U.S. dollars.",
    "The financial statements are expressed in US dollars.",
    "We present our consolidated financial statements in U.S. dollars.",
    "Our reporting currency is the U.S. dollar.",
    "The Company's presentation currency is the United States dollar.",
    "The U.S. dollar is our reporting currency.",
    "The consolidated financial statements have been prepared in accordance with GAAP and are presented in U.S. dollars.",
]


@pytest.mark.parametrize("sentence", GOVERNING_SENTENCES)
def test_c2b_a_document_statement_governing_the_statements_is_accepted(sentence):
    table = run(with_text(no_currency_doc(), sentence))
    assert six(table) and kinds(table) == {"document_statement_usd"}
    excerpt = next(e for e in by_id(table)["REV_2024"].excerpts if e.kind == "currency")
    assert sentence.split(".")[0][:25] in excerpt.text


def test_c2b_the_governing_sentence_among_commentary_is_what_gets_cited():
    html = with_text(with_text(no_currency_doc(), "The USD strengthened against the euro."), "Our reporting currency is the U.S. dollar.")
    excerpt = next(e for e in by_id(run(html))["REV_2024"].excerpts if e.kind == "currency")
    assert excerpt.text == "Our reporting currency is the U.S. dollar."


def test_c2_header_evidence_takes_precedence_over_a_document_statement():
    inc = income(scale="(In millions of U.S. dollars)")
    cf = cashflow(scale="(In millions of U.S. dollars)")
    assert kinds(run(make_doc(inc, cf, currency=True))) == {"statement_header_usd"}


@pytest.mark.parametrize("sentence", [
    "The consolidated financial statements are presented in U.S. dollars, and our senior notes are denominated in U.S. dollars.",
    "Our reporting currency is the U.S. dollar; the dollar strengthened against other currencies this year.",
    "The financial statements are presented in U.S. dollars and foreign exchange rates affect results.",
])
def test_c2b_a_governing_sentence_mixed_with_debt_or_fx_commentary_is_not_clear_evidence(sentence):
    table = run(with_text(no_currency_doc(), sentence))
    assert table.facts == [] and "missing_currency_evidence" in codes(table)


def test_c2_debt_denomination_in_another_currency_is_not_a_conflict_about_the_statements_currency():
    html = with_text(with_text(no_currency_doc(), "Our reporting currency is the U.S. dollar."), "Our euro notes are denominated in euros.")
    table = run(html)
    assert six(table) and kinds(table) == {"document_statement_usd"}


def test_c2_subsidiary_functional_currency_in_another_currency_is_not_a_conflict_either():
    html = with_text(with_text(no_currency_doc(), "Our reporting currency is the U.S. dollar."),
                     "The functional currency of our European subsidiary is the euro.")
    assert six(run(html))


# ---- conflicts stay unresolved

@pytest.mark.parametrize("sentence", [
    "The financial statements are presented in Canadian dollars.",
    "Our reporting currency is the euro.",
])
def test_c2_a_conflicting_document_statement_blocks_even_with_usd_header_evidence(sentence):
    html = with_text(make_doc(income(scale="(In millions of U.S. dollars)"), cashflow(scale="(In millions of U.S. dollars)"), currency=False), sentence)
    table = run(html)
    assert table.facts == [] and "currency_contradiction" in codes(table)


def test_c2_conflicting_document_statements_block_each_other():
    html = with_text(with_text(no_currency_doc(), "The consolidated financial statements are presented in U.S. dollars."),
                     "The consolidated financial statements are presented in Canadian dollars.")
    table = run(html)
    assert table.facts == [] and "currency_contradiction" in codes(table)


def test_c2_a_non_usd_header_blocks_even_with_a_usd_document_statement():
    table = run(make_doc(income(scale="(In millions of Canadian dollars)"), cashflow(scale="(In millions of Canadian dollars)"), currency=True))
    assert table.facts == [] and "currency_contradiction" in codes(table)


# ---- (c) fallback: the candidate amount's own inline-XBRL unit

def ixdoc(unit_ref="usd", units=None, html_attrs=IX_NAMESPACES, currency=False, header=True, scope=True) -> bytes:
    units = units if units is not None else [unit("usd")]
    inc = income([ixrow("Total revenue", ["1,000", "900"], unit_ref), ixrow("Net income", ["250", "200"], unit_ref)])
    cf = cashflow([ixrow("Net income", ["250", "200"], unit_ref), ixrow("Net cash from operations", ["400", "350"], unit_ref, dollar=False)])
    return make_doc(inc, cf, currency=currency, html_attrs=html_attrs, header=ix_header(*units) if header else "")


def test_c2c_the_amounts_own_unit_resolving_to_iso4217_usd_is_accepted_and_fully_preserved():
    table = run(ixdoc())
    assert six(table) and kinds(table) == {"ixbrl_unit_usd"}
    fact = by_id(table)["REV_2024"]
    assert (fact.currency_unit_id, fact.currency_unit_measure) == ("usd", "iso4217:USD")
    assert "xbrli:unit[0]" in fact.currency_unit_locator and "'usd'" in fact.currency_unit_locator
    excerpt = next(e for e in fact.excerpts if e.kind == "currency")
    assert "iso4217:USD" in excerpt.text and "unitRef='usd'" in excerpt.where
    assert f"table {fact.locator.table_index} row {fact.locator.row_index} cell {fact.locator.cell_index}" in excerpt.where
    FactTable.model_validate(json.loads(table.model_dump_json()))


def test_c2c_it_is_only_a_fallback_header_or_document_evidence_wins():
    assert kinds(run(ixdoc(currency=True))) == {"document_statement_usd"}


def test_c2c_commentary_does_not_replace_the_fallback():
    assert kinds(run(with_text(ixdoc(), "The USD strengthened against the euro."))) == {"ixbrl_unit_usd"}


def test_c2c_an_unrelated_usd_unit_elsewhere_is_insufficient():
    # a USD unit exists, but the amounts reference a different, undefined unit id
    table = run(ixdoc(unit_ref="u9", units=[unit("usd")]))
    assert table.facts == [] and "missing_currency_evidence" in codes(table)


def test_c2c_amounts_without_a_unitref_are_insufficient_even_with_a_usd_unit_defined():
    table = run(ixdoc(unit_ref=None))
    assert table.facts == [] and "missing_currency_evidence" in codes(table)


def test_c2c_untagged_amounts_are_insufficient_even_with_a_usd_unit_defined():
    html = make_doc(income(), cashflow(), currency=False, html_attrs=IX_NAMESPACES, header=ix_header(unit("usd")))
    table = run(html)
    assert table.facts == [] and "missing_currency_evidence" in codes(table)


def test_c2c_only_the_tagged_cells_with_a_usd_unit_produce_facts():
    inc = income([row("Total revenue", [("1,000", {"name": "x", "scale": "6", "unitRef": "usd", "contextRef": "c"}), "900"], dollar=True),
                  row("Net income", ["250", "200"], dollar=True)])
    table = run(make_doc(inc, cashflow(), currency=False, html_attrs=IX_NAMESPACES, header=ix_header(unit("usd"))))
    assert "REV_2024" in by_id(table) and "REV_2023" not in by_id(table)  # the untagged FY2023 cell has no governing evidence


@pytest.mark.parametrize("units", [
    [unit("usd", "iso4217:EUR")],
    [unit("usd", "xbrli:shares")],
    [unit("usd", "iso4217:usd")],            # wrong case
    [unit("usd", "iso4217:USD iso4217:USD")],  # not a simple unit (two measures)
    ['<xbrli:unit id="usd"><xbrli:divide><xbrli:unitNumerator><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unitNumerator>'
     '<xbrli:unitDenominator><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unitDenominator></xbrli:divide></xbrli:unit>'],
    [unit("usd"), unit("usd")],               # duplicate id: ambiguous
])
def test_c2c_only_a_simple_iso4217_usd_unit_counts(units):
    table = run(ixdoc(units=units))
    assert table.facts == []


def test_c2c_a_non_usd_iso_unit_is_a_contradiction_not_just_missing_evidence():
    assert "currency_contradiction" in codes(run(ixdoc(units=[unit("usd", "iso4217:EUR")])))


def test_c2c_the_unit_namespace_must_be_the_iso4217_namespace():
    table = run(ixdoc(html_attrs=IX_NAMESPACES.replace("http://www.xbrl.org/2003/iso4217", "http://example.invalid/not-iso")))
    assert table.facts == []
    assert table.facts == [] and "missing_currency_evidence" in codes(table)


def test_c2c_an_undeclared_measure_prefix_is_insufficient():
    assert run(ixdoc(html_attrs="")).facts == []


def test_c2c_a_unit_outside_ix_resources_is_ignored():
    stray = f"<div>{unit('usd')}</div>"
    html = make_doc(income([ixrow("Total revenue", ["1,000", "900"])]), cashflow(), currency=False, html_attrs=IX_NAMESPACES, header=stray)
    assert run(html).facts == []


def test_c2c_a_valid_usd_unit_does_not_override_a_document_statement_in_another_currency():
    table = run(with_text(ixdoc(), "The consolidated financial statements are presented in Canadian dollars."))
    assert table.facts == [] and "currency_contradiction" in codes(table)


def test_c2c_a_valid_usd_unit_does_not_override_a_non_usd_statement_header():
    inc = income([ixrow("Total revenue", ["1,000", "900"]), ixrow("Net income", ["250", "200"])], scale="(In millions of Canadian dollars)")
    cf = cashflow([ixrow("Net income", ["250", "200"]), ixrow("Net cash from operations", ["400", "350"], dollar=False)],
                  scale="(In millions of Canadian dollars)")
    table = run(make_doc(inc, cf, currency=False, html_attrs=IX_NAMESPACES, header=ix_header(unit("usd"))))
    assert table.facts == [] and "currency_contradiction" in codes(table)


def test_c2c_each_amount_uses_its_own_unit_reference():
    inc = income([ixrow("Total revenue", ["1,000", "900"], "usd"), ixrow("Net income", ["250", "200"], "eur")])
    cf = cashflow([ixrow("Net income", ["250", "200"], "eur"), ixrow("Net cash from operations", ["400", "350"], "usd", dollar=False)])
    html = make_doc(inc, cf, currency=False, html_attrs=IX_NAMESPACES, header=ix_header(unit("usd"), unit("eur", "iso4217:EUR")))
    table = run(html)
    assert {"REV_2024", "REV_2023", "OCF_2024", "OCF_2023"} <= set(by_id(table))
    assert "NI_2024" not in by_id(table) and "currency_contradiction" in codes(table)


def test_c2_a_usd_header_does_not_override_a_non_usd_unit_on_the_amount():
    inc = income([ixrow("Total revenue", ["1,000", "900"], "eur"), ixrow("Net income", ["250", "200"], "usd")], scale="(In millions of U.S. dollars)")
    cf = cashflow([ixrow("Net income", ["250", "200"], "usd"), ixrow("Net cash from operations", ["400", "350"], "usd")], scale="(In millions of U.S. dollars)")
    html = make_doc(inc, cf, currency=False, html_attrs=IX_NAMESPACES, header=ix_header(unit("usd"), unit("eur", "iso4217:EUR")))
    table = run(html)
    assert "REV_2024" not in by_id(table) and "currency_contradiction" in codes(table)
    assert "NI_2024" in by_id(table)


# ---- schema and evaluator

def test_c2_the_old_dollar_sign_policy_kinds_no_longer_exist():
    from finreport.schemas import Fact
    allowed = Fact.model_fields["currency_evidence_kind"].annotation.__args__
    assert set(allowed) == {"statement_header_usd", "document_statement_usd", "ixbrl_unit_usd"}


def test_c2_a_unit_based_fact_must_carry_its_unit_details():
    table = json.loads(run(ixdoc()).model_dump_json())
    del table["facts"][0]["currency_unit_locator"]
    with pytest.raises(Exception):
        FactTable.model_validate(table)
    table = json.loads(run(standard_doc()).model_dump_json())
    table["facts"][0]["currency_unit_id"] = "usd"  # unit details are only for the unit-based kind
    with pytest.raises(Exception):
        FactTable.model_validate(table)


def _known(prefix):
    values = {f["evidence_id"]: f["reported_value"] for f in EXPECTED["facts"]}
    return [f"{values[prefix + '_2024']:,}", f"{values[prefix + '_2023']:,}"]


def _expected_numbers_doc(variant: str) -> tuple[bytes, bool]:
    """(document, wrapper_adds_governing_sentence). Same numbers as the frozen expectations; synthetic, evaluator-unit use only."""
    if variant == "header":
        scale = "(In millions of U.S. dollars, except per share amounts)"
        return make_doc(income([row("Total revenue", _known("REV"), dollar=True), row("Net income", _known("NI"), dollar=True)], scale=scale),
                        cashflow([row("Net income", _known("NI"), dollar=True), row("Net cash from operations", _known("OCF"))], scale=scale),
                        currency=False), False
    if variant == "document":
        return make_doc(income([row("Total revenue", _known("REV"), dollar=True), row("Net income", _known("NI"), dollar=True)]),
                        cashflow([row("Net income", _known("NI"), dollar=True), row("Net cash from operations", _known("OCF"))]),
                        currency=False), True
    inc = income([ixrow("Total revenue", _known("REV")), ixrow("Net income", _known("NI"))])
    cf = cashflow([ixrow("Net income", _known("NI")), ixrow("Net cash from operations", _known("OCF"), dollar=False)])
    return make_doc(inc, cf, currency=False, html_attrs=IX_NAMESPACES, header=ix_header(unit("usd"))), False


@pytest.mark.parametrize("variant,expected_kind", [("header", "statement_header_usd"), ("document", "document_statement_usd"),
                                                   ("ixbrl", "ixbrl_unit_usd")])
def test_c2_evaluate_accepts_each_governing_kind_end_to_end_and_reports_it(tmp_path, variant, expected_kind):
    html, wrapper_adds_sentence = _expected_numbers_doc(variant)
    synthetic_microsoft_shaped_root(tmp_path, html, governing_currency=wrapper_adds_sentence)
    assert main(["--root", str(tmp_path), "extract"]) == 0
    facts = json.loads((tmp_path / "output" / "fact_table.json").read_text())
    assert {f["currency_evidence_kind"] for f in facts["facts"]} == {expected_kind}
    if expected_kind == "ixbrl_unit_usd":
        assert {(f["currency_unit_id"], f["currency_unit_measure"]) for f in facts["facts"]} == {("usd", "iso4217:USD")}
    assert main(["--root", str(tmp_path), "evaluate"]) == 0
    report = evaluation_report(tmp_path)
    assert report["acceptance"]["accepted"] is True
    assert report["acceptance"]["informational"]["currency_evidence_kinds"] == [expected_kind]


def test_c2_the_review_probes_negative_input_cannot_be_accepted_by_evaluate():
    """The exact shape of the review's probe: negative input, correct numbers, matching manifest checksum."""
    for html in (numbers_doc(),
                 with_text(numbers_doc(), "The USD strengthened against the euro."),
                 with_text(numbers_doc(), "Our debt is denominated in U.S. dollars.")):
        sha = hashlib.sha256(html).hexdigest()
        table = extract_facts(html, MSFT, sha)
        check = {"verified": True, "problems": [], "recorded": {"filename": "f", "byte_count": len(html), "sha256": sha},
                 "actual": {"filename": "f", "byte_count": len(html), "sha256": sha}}
        result = evaluate(EXPECTED, json.loads(table.model_dump_json()), manifest_sha256=sha, source_check=check)
        assert result["acceptance"]["accepted"] is False
        assert {"expected_fact_missing", "blocking_extraction_issues"} <= {r["code"] for r in result["acceptance"]["reasons"]}
