"""CLI tests for the answer / demo / live / eval-answers commands, snapshot mode, and the answer-evaluation harness.

Uses the committed example root (examples/msft_fy2024: fact table + reviewed-snapshot record, no filing bytes). No network, no model
process, no credential. Exposed development tests."""

import json
import shutil
from pathlib import Path

import pytest

from finreport import answer_eval, cli
from finreport.answers import FactBook, answer_question
from finreport.cli import main
from finreport.narrative import ProviderUnavailable, RawOutput

ROOT = Path(__file__).parents[2]
EX = ROOT / "examples" / "msft_fy2024"
Q1 = "What was Microsoft's consolidated revenue in FY2024?"
Q2 = "How much did consolidated revenue change from FY2023 to FY2024?"
Q3 = "What will Microsoft's consolidated revenue be in FY2027?"


def run(capsys, *argv):
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def clone_example(tmp_path, *, snapshot=True):
    root = tmp_path / "ex"
    (root / "output").mkdir(parents=True)
    shutil.copy(EX / "output" / "fact_table.json", root / "output" / "fact_table.json")
    if snapshot:
        (root / "data").mkdir()
        shutil.copy(EX / "data" / "reviewed_snapshot.json", root / "data" / "reviewed_snapshot.json")
    return root


# ------------------------------------------------------------------ demo / answer


def test_demo_runs_offline_from_the_example_root_and_all_three_behave(capsys):
    code, out, _ = run(capsys, "--root", str(EX), "demo")
    assert code == 0
    assert "USD 245,122 million" in out and "USD 33,207 million" in out and "15.67%" in out
    assert "forecast_not_supported" in out and "No forecast is made" in out
    assert out.count("SCRIPTED TEMPLATE - not a model") == 2 and "LIVE MODEL" not in out
    assert "reviewed_snapshot_fact_table_only" not in out and "WEAKER" in out      # the weaker level is stated plainly
    assert "demo result: all three questions behaved as specified" in out


def test_demo_default_root_falls_back_to_the_bundled_example(capsys, monkeypatch):
    monkeypatch.chdir(ROOT)
    assert run(capsys, "demo")[0] == 0


def test_demo_without_any_fact_table_exits_2(tmp_path, capsys):
    code, _, err = run(capsys, "--root", str(tmp_path), "demo")
    assert code == 2 and "no fact table" in err


@pytest.mark.parametrize("q,code,status", [(Q1, 0, "answered"), (Q2, 0, "answered"), (Q3, 1, "unresolved")])
def test_answer_json_exit_codes_and_contract(capsys, q, code, status):
    c, out, _ = run(capsys, "--root", str(EX), "answer", q, "--json")
    data = json.loads(out)
    assert c == code and data["answer"]["status"] == status
    assert data["answer"]["source"]["verification_level"] == "reviewed_snapshot_fact_table_only"
    if status == "answered":
        assert data["narrative"]["status"] == "validated" and data["narrative"]["adapter"] == "scripted"
    else:
        assert data["answer"]["evidence"] == [] and data["answer"]["value"] is None and data["narrative"]["claims"] == []


def test_answer_text_shows_question_status_facts_calculation_and_excerpts(capsys):
    _, out, _ = run(capsys, "--root", str(EX), "answer", Q2)
    for needle in ("Question :", "Status   : answered", "[REV_2023]", "[REV_2024]", "INCOME STATEMENTS", "row text: Total revenue | 245,122",
                   "Calculation (Decimal", "ROUND_HALF_UP = 15.67%", "scale: (In millions", "A search for later amendments"):
        assert needle in out, needle


def test_ask_stays_strict_and_refuses_without_the_filing_bytes(capsys):
    code, out, _ = run(capsys, "--root", str(EX), "ask", Q1)
    assert code in (2, 5)      # no manifest/cache in the example root: strict mode never falls back to the snapshot
    if code == 5:
        assert json.loads(out)["status"] == "refused"


def test_fact_table_without_snapshot_record_is_refused_not_trusted(tmp_path, capsys):
    root = clone_example(tmp_path, snapshot=False)
    code, out, _ = run(capsys, "--root", str(root), "answer", Q1, "--json")
    assert code == 5 and json.loads(out)["answer"]["status"] == "refused"


def test_snapshot_mode_rejects_a_fact_table_that_does_not_match_the_recorded_hash(tmp_path, capsys):
    root = clone_example(tmp_path)
    ft = json.loads((root / "output" / "fact_table.json").read_text())
    ft["source"]["checksum"] = "0" * 64
    (root / "output" / "fact_table.json").write_text(json.dumps(ft))
    code, out, _ = run(capsys, "--root", str(root), "answer", Q1, "--json")
    assert code in (2, 5) and (code == 2 or json.loads(out)["answer"]["status"] == "refused")


def test_snapshot_mode_rejects_a_wrong_accession_record(tmp_path, capsys):
    root = clone_example(tmp_path)
    p = root / "data" / "reviewed_snapshot.json"
    snap = json.loads(p.read_text())
    snap["accession"] = "0000000000-00-000000"
    p.write_text(json.dumps(snap))
    code, out, _ = run(capsys, "--root", str(root), "answer", Q1, "--json")
    assert code == 5 and "accession" in json.dumps(json.loads(out)["answer"]["source"]["verification_problems"])


def test_example_root_contains_no_filing_bytes_or_credentials():
    assert not list(EX.rglob("*.htm")) and not list(EX.rglob("*.html"))
    blob = "".join(p.read_text(errors="ignore") for p in EX.rglob("*") if p.is_file())
    assert "sk-" not in blob and "ANTHROPIC" not in blob and "SEC_USER_AGENT" not in blob


# ------------------------------------------------------------------ live


class FakeAdapter:
    name = "fake-provider"
    model = "fake-1"

    def __init__(self, behaviour):
        self.behaviour = behaviour

    def generate(self, request):
        if isinstance(self.behaviour, Exception):
            raise self.behaviour
        return RawOutput(self.behaviour, "fake-1", {"input_tokens": 1, "output_tokens": 2})


GOOD = json.dumps({"claims": [{"text": "Microsoft Corporation's consolidated revenue for FY2024 was USD 245,122 million.",
                               "evidence_ids": ["REV_2024"], "calculation_id": None}]})


def patch_provider(monkeypatch, behaviour):
    monkeypatch.setattr(cli, "_provider", lambda args: FakeAdapter(behaviour))


def test_live_success_prints_the_model_label_and_records_the_run(monkeypatch, capsys, tmp_path):
    patch_provider(monkeypatch, GOOD)
    code, out, err = run(capsys, "--root", str(EX), "live", Q1, "--record", str(tmp_path / "runs"))
    assert code == 0 and "LIVE MODEL fake-1 via fake-provider" in out and "SCRIPTED" not in out
    rec = json.loads(next((tmp_path / "runs").glob("*.json")).read_text())
    assert rec["narrative"]["status"] == "validated" and rec["narrative"]["model"] == "fake-1" and rec["answer"]["status"] == "answered"
    assert "prompt_sha256" in rec["narrative"] and "<verified_data>" not in json.dumps(rec)   # the prompt is hashed, not stored


def test_live_provider_unavailable_exits_7_and_still_prints_the_structured_answer(monkeypatch, capsys):
    patch_provider(monkeypatch, ProviderUnavailable("ANTHROPIC_API_KEY is not set"))
    code, out, _ = run(capsys, "--root", str(EX), "live", Q1)
    assert code == 7 and "Status   : answered" in out and "NOT GENERATED - provider unavailable" in out and "USD 245,122 million" in out


def test_live_rejected_narrative_exits_8_and_shows_no_model_text(monkeypatch, capsys):
    bad = json.dumps({"claims": [{"text": "Revenue for FY2024 was USD 999 million.", "evidence_ids": ["REV_2024"], "calculation_id": None}]})
    patch_provider(monkeypatch, bad)
    code, out, _ = run(capsys, "--root", str(EX), "live", Q1, "--max-attempts", "1")
    assert code == 8 and "REJECTED by validation" in out and "USD 999" not in out


def test_live_on_unsupported_question_never_calls_the_provider(monkeypatch, capsys):
    patch_provider(monkeypatch, AssertionError("must not be called"))
    code, out, _ = run(capsys, "--root", str(EX), "live", Q3)
    assert code == 1 and "none generated" in out


def test_live_attempt_bound_is_clamped(monkeypatch, capsys):
    calls = []

    class Counting(FakeAdapter):
        def generate(self, request):
            calls.append(1)
            return RawOutput("garbage", "fake-1", {})

    monkeypatch.setattr(cli, "_provider", lambda args: Counting("x"))
    code, *_ = run(capsys, "--root", str(EX), "live", Q1, "--max-attempts", "99")
    assert code == 8 and len(calls) == 3


def test_live_default_provider_missing_binary_is_exit_7(monkeypatch, capsys):
    monkeypatch.setattr("shutil.which", lambda _: None)
    code, out, _ = run(capsys, "--root", str(EX), "live", Q1)
    assert code == 7 and "not found" in out


# ------------------------------------------------------------------ answer evaluation


def test_frozen_case_file_matches_its_recorded_hash():
    doc, info = answer_eval.load_cases(ROOT)
    assert info["matches_freeze_record"] and len(doc["cases"]) == 40
    assert (ROOT / "evaluation" / "answer_cases.sha256").read_text().split()[0] == info["cases_sha256"]


def test_eval_answers_all_pass_on_the_example_root_with_separate_dimensions(capsys, tmp_path):
    out_file = tmp_path / "r.json"
    code, out, _ = run(capsys, "--root", str(EX), "eval-answers", "--cases-root", str(ROOT), "--out", str(out_file))
    rep = json.loads(out_file.read_text())
    assert code == 0 and rep["failures"] == []
    d = rep["dimensions"]
    assert (d["1_value_correctness"]["passed"], d["1_value_correctness"]["denominator"]) == (16, 16)
    assert (d["2_calculation_correctness"]["passed"], d["2_calculation_correctness"]["denominator"]) == (8, 8)
    assert (d["3_citation_validity"]["passed"], d["3_citation_validity"]["denominator"]) == (40, 40)
    assert (d["4_appropriate_non_answers"]["passed"], d["4_appropriate_non_answers"]["denominator"]) == (24, 24)
    assert (d["5_scripted_narrative_validation"]["passed"], d["5_scripted_narrative_validation"]["denominator"]) == (16, 16)
    assert rep["verification_level"] == "reviewed_snapshot_fact_table_only" and "not independent" in rep["authorship"].lower()
    assert "5_scripted_narrative_validation" in out and "not independent" in out.lower()


def test_eval_exits_5_when_the_case_file_differs_from_the_freeze_record(tmp_path, capsys):
    shutil.copytree(ROOT / "evaluation", tmp_path / "evaluation", ignore=shutil.ignore_patterns("__pycache__"))
    p = tmp_path / "evaluation" / "answer_cases.json"
    p.write_text(p.read_text().replace("USD 245,122 million", "USD 245,123 million", 1))
    code, out, _ = run(capsys, "--root", str(EX), "eval-answers", "--cases-root", str(tmp_path))
    assert code == 5 and "DOES NOT MATCH" in out


def test_eval_harness_detects_a_wrong_value_a_bad_citation_and_a_false_answer(monkeypatch, capsys):
    real = answer_eval.answer_question

    def broken(question, book):
        a = real(question, book)
        if a.status == "answered" and a.question_kind == "lookup" and "FY2023" in question:
            return a.model_copy(update={"value": a.value.model_copy(update={"display": "USD 1 million"})})
        if a.status == "answered" and a.question_kind == "change" and "net income" in question and "FY2023 to" in question:
            return a.model_copy(update={"evidence": list(reversed(a.evidence))[:1] + a.evidence[:1]})
        if a.reason_code == "forecast_not_supported" and "FY2027" in question and "change" not in question:
            return real("What was Microsoft's consolidated revenue in FY2024?", book)
        return a

    monkeypatch.setattr(answer_eval, "answer_question", broken)
    code, out, _ = run(capsys, "--root", str(EX), "eval-answers", "--cases-root", str(ROOT))
    assert code == 1 and "Failures:" in out and "false answers: ['U01']" in out
    assert "L02" in out and "L04" in out


def test_production_modules_never_read_the_answer_cases():
    for name in ("answers.py", "narrative.py", "display.py", "extract.py", "parsing.py", "ingest.py"):
        text = (ROOT / "src" / "finreport" / name).read_text()
        assert "answer_cases" not in text and "answer_eval" not in text, name


# ------------------------------------------------------------------ added after the first mutation run of the finish-phase modules
# (seven mutations survived: display direction, four harness scoring rules, two snapshot-mode rules)


def book_from_example():
    from finreport.answers import load_factbook
    return load_factbook(EX, allow_snapshot=True)


def test_concise_answer_for_a_decrease_says_decreased_and_drops_the_sign():
    from finreport.display import concise_answer
    a = answer_question("How much did net income change from FY2024 to FY2023?", book_from_example())
    text = concise_answer(a)
    assert a.value.direction == "decrease"
    assert "decreased by USD 15,775 million (17.90%) from FY2024 to FY2023" in text and "increased" not in text and "-" not in text.split("USD")[1]


def eval_with(monkeypatch, capsys, patch):
    real = answer_eval.answer_question
    monkeypatch.setattr(answer_eval, "answer_question", lambda q, b: patch(q, real(q, b), b))
    return run(capsys, "--root", str(EX), "eval-answers", "--cases-root", str(ROOT))


def test_harness_scores_the_reason_code(monkeypatch, capsys):
    def patch(q, a, b):
        return a.model_copy(update={"reason_code": "unsupported_question"}) if "FY2027" in q and a.status == "unresolved" and "change" not in q else a
    code, out, _ = eval_with(monkeypatch, capsys, patch)
    assert code == 1 and "U01" in out


def test_harness_checks_that_the_cited_row_text_contains_the_amount(monkeypatch, capsys):
    def patch(q, a, b):
        if a.status == "answered" and a.question_kind == "lookup" and "FY2024" in q and "net income" in q:
            ev = a.evidence[0].model_copy(update={"amount_excerpt": "Net income | something else entirely"})
            return a.model_copy(update={"evidence": [ev]})
        return a
    code, out, _ = eval_with(monkeypatch, capsys, patch)
    assert code == 1 and "L03" in out and "does not contain the amount" in out


def test_harness_checks_the_expected_evidence_ids(monkeypatch, capsys):
    other = answer_question("What was Microsoft's consolidated revenue in FY2023?", book_from_example()).evidence[0]

    def patch(q, a, b):   # a valid, resolvable, amount-bearing reference - but to the wrong year
        return a.model_copy(update={"evidence": [other]}) if q == "What was Microsoft's consolidated revenue in FY2024?" and a.status == "answered" else a
    code, out, _ = eval_with(monkeypatch, capsys, patch)
    assert code == 1 and "L01" in out and "expected ['REV_2024']" in out


def test_harness_scores_the_percentage_separately(monkeypatch, capsys):
    def patch(q, a, b):
        if a.status == "answered" and a.question_kind == "change" and "revenue" in q and "FY2023 to FY2024" in q and "consolidated" in q:
            return a.model_copy(update={"value": a.value.model_copy(update={"percent_change_display": "15.68%"})})
        return a
    code, out, _ = eval_with(monkeypatch, capsys, patch)
    assert code == 1 and "C01" in out and "pct=False" in out


def test_snapshot_mode_requires_the_snapshot_hash_to_equal_the_table_hash():
    from finreport.answers import FactBook, _load_snapshot
    from finreport.schemas import FactTable
    table = FactTable.model_validate_json((EX / "output" / "fact_table.json").read_text(encoding="utf-8"))
    snap = dict(_load_snapshot(EX), sha256="1" * 64)
    book = FactBook.from_reviewed_snapshot(table, snap)
    assert not book.verified and "table_checksum_differs_from_reviewed_snapshot" in book.problems
    assert book.source().verification_level == "unverified"


def test_a_present_but_wrong_cached_file_is_never_excused_by_the_snapshot(tmp_path, capsys):
    root = clone_example(tmp_path)
    (root / "data" / "cache").mkdir()
    shutil.copy(ROOT / "tests" / "real" / "msft_fy2024" / "source_manifest_imported.json", root / "data" / "source_manifest.json")
    (root / "data" / "cache" / "msft-20240630.htm").write_bytes(b"<html>not the filing</html>")
    code, out, _ = run(capsys, "--root", str(root), "answer", Q1, "--json")
    data = json.loads(out)["answer"]
    assert code == 5 and data["status"] == "refused" and data["source"]["verification_level"] == "unverified"
