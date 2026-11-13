import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from finreport.cli import main
from finreport.evaluation import evaluate
from finreport.ingest import cache_path, sha256_file
from finreport.manifest import Retrieval, initial_manifest, load_manifest, save_manifest
from helpers import standard_doc

EXPECTED = json.loads((Path(__file__).parents[2] / "evaluation" / "expected_facts.json").read_text())


# ------------------------------------------------------------------ CLI


def test_init_then_fetch_without_user_agent_is_pending_exit_3(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    assert main(["--root", str(tmp_path), "init"]) == 0
    assert load_manifest(tmp_path / "data" / "source_manifest.json").status == "pending_download"
    assert main(["--root", str(tmp_path), "fetch"]) == 3
    assert "SEC_USER_AGENT is not set" in capsys.readouterr().err
    assert main(["--root", str(tmp_path), "extract"]) == 2  # nothing to extract


def test_extract_and_evaluate_roundtrip_on_synthetic_cache(tmp_path):
    main(["--root", str(tmp_path), "init"])
    mpath = tmp_path / "data" / "source_manifest.json"
    manifest = load_manifest(mpath)
    path = cache_path(tmp_path, manifest.target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(standard_doc())
    retrieval = Retrieval(
        requested_url=manifest.target.url, final_url=manifest.target.url, retrieved_at_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
        http_status=200, content_type="text/html", cached_filename=path.name, byte_count=path.stat().st_size,
        sha256=sha256_file(path), declared_user_agent=True)
    save_manifest(manifest.model_copy(update={"status": "downloaded", "retrieval": retrieval}), mpath)

    assert main(["--root", str(tmp_path), "extract"]) == 0
    table = json.loads((tmp_path / "output" / "fact_table.json").read_text())
    assert len(table["facts"]) == 6 and (tmp_path / "output" / "evidence_excerpts.md").read_text().count("## ") == 6
    assert main(["--root", str(tmp_path), "extract"]) == 0  # idempotent

    (tmp_path / "evaluation").mkdir()
    (tmp_path / "evaluation" / "expected_facts.json").write_text(json.dumps(EXPECTED))
    # synthetic numbers differ from the frozen MSFT expectations, so evaluation must say so
    assert main(["--root", str(tmp_path), "evaluate"]) == 1
    report = json.loads((tmp_path / "output" / "evaluation_report.json").read_text())
    assert report["numeric_fact_correctness"] == "0/6" and report["unexpected_extra_facts"] == []


def test_extract_refuses_on_checksum_mismatch(tmp_path, capsys):
    main(["--root", str(tmp_path), "init"])
    mpath = tmp_path / "data" / "source_manifest.json"
    manifest = load_manifest(mpath)
    path = cache_path(tmp_path, manifest.target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(standard_doc())
    retrieval = Retrieval(requested_url=manifest.target.url, final_url=manifest.target.url,
                          retrieved_at_utc=datetime(2026, 1, 1, tzinfo=timezone.utc), http_status=200, content_type="text/html",
                          cached_filename=path.name, byte_count=path.stat().st_size, sha256="00" * 32, declared_user_agent=True)
    save_manifest(manifest.model_copy(update={"status": "downloaded", "retrieval": retrieval}), mpath)
    assert main(["--root", str(tmp_path), "extract"]) == 5


# ------------------------------------------------------------------ evaluation scoring


def perfect_table():
    shared, periods = EXPECTED["shared"], EXPECTED["periods"]
    facts = []
    for f in EXPECTED["facts"]:
        facts.append({
            "evidence_id": f["evidence_id"], "metric": f["metric"], "original_label": f["original_label"],
            "reported_value": str(f["reported_value"]), "normalized_value": str(f["normalized_value_usd"]),
            "currency": shared["currency"], "scale_multiplier": shared["scale_multiplier"],
            "period": {"label": f["period"], **{k: periods[f["period"]][k] for k in ("start", "end")}},
            "scope": "consolidated", "accounting_basis": shared["accounting_basis"],
            "locator": {**f["locator"], "section": f["locator"]["section"], "statement": f["locator"]["statement"].upper()},
        })
    return {"facts": facts, "conflicts": [], "issues": []}


def test_perfect_table_scores_six_of_six():
    r = evaluate(EXPECTED, perfect_table())
    assert r["numeric_fact_correctness"] == "6/6" and r["unexpected_extra_facts"] == []


@pytest.mark.parametrize("mutate,failed_check", [
    (lambda f: f.update(reported_value="-245122", normalized_value="-245122000000"), "value_and_sign"),
    (lambda f: f.update(reported_value="245122000", normalized_value="245122000000"), "value_and_sign"),
    (lambda f: f.update(scale_multiplier=1000, normalized_value="245122000000"), "units"),
    (lambda f: f.update(currency="EUR"), "units"),
    (lambda f: f["period"].update(start="2022-07-01", end="2023-06-30"), "period"),
    (lambda f: f.update(scope="segment"), "scope"),
    (lambda f: f.update(normalized_value="245122"), "normalized_value"),
])
def test_each_mismatch_class_fails_that_fact_only(mutate, failed_check):
    table = perfect_table()
    mutate(table["facts"][0])
    r = evaluate(EXPECTED, table)
    assert r["numeric_fact_correctness"] == "5/6"
    assert r["per_fact"][0]["checks"][failed_check] is False and all(x["passed"] for x in r["per_fact"][1:])


def test_missing_fact_and_extra_facts_are_reported():
    table = perfect_table()
    dropped = table["facts"].pop(0)
    extra = copy.deepcopy(dropped)
    extra.update(evidence_id="REV_2022", period={"label": "FY2022", "start": "2021-07-01", "end": "2022-06-30"})
    table["facts"].append(extra)
    dup = copy.deepcopy(table["facts"][0])
    dup["evidence_id"] = "NI_2024_dup"
    table["facts"].append(dup)
    r = evaluate(EXPECTED, table)
    assert r["numeric_fact_correctness"] == "5/6" and not r["per_fact"][0]["found"]
    assert r["unexpected_extra_facts"] == ["REV_2022", "NI_2024_dup"]


def test_locator_disagreement_is_informational_not_a_failure():
    table = perfect_table()
    table["facts"][0]["locator"]["printed_page"] = 57
    r = evaluate(EXPECTED, table)
    assert r["numeric_fact_correctness"] == "6/6" and r["per_fact"][0]["locator_info"]["printed_page"]["match"] is False
