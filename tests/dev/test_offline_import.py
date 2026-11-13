"""Offline ingestion of an externally downloaded filing. No network; nothing about HTTP is invented."""

import hashlib
import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from finreport.cli import main
from finreport.ingest import CacheMismatch, HttpResponse, InvalidFiling, cache_is_valid, cache_path, fetch_filing, import_external_file
from finreport.manifest import ExternalImport, SourceManifest, initial_manifest, load_manifest, save_manifest
from helpers import standard_doc

METHOD = "saved from a browser by the operator, then hashed locally with sha256sum"
FILING = b"<html><body>Examplecorp FORM 10-K fiscal year ended June 30, 2024 " + b"x" * 500 + b"</body></html>"
NOW = lambda: datetime(2026, 5, 6, 7, 8, 9, tzinfo=timezone.utc)  # noqa: E731


def manifest() -> SourceManifest:
    m = initial_manifest()
    return m.model_copy(update={"target": m.target.model_copy(update={"company": "Examplecorp"})})


def do_import(tmp_path, body=FILING, **kw):
    src = tmp_path / "downloads" / "some-name.htm"
    src.parent.mkdir(exist_ok=True)
    src.write_bytes(body)
    kw.setdefault("acquisition_method", METHOD)
    return import_external_file(manifest(), tmp_path, src, now=NOW, min_bytes=100, **kw), src


def test_import_records_actual_hash_and_method_without_inventing_http_facts(tmp_path):
    m, src = do_import(tmp_path)
    rec = m.external_import
    assert m.status == "imported_offline" and m.retrieval is None
    assert rec.sha256 == hashlib.sha256(FILING).hexdigest() == hashlib.sha256(cache_path(tmp_path, m.target).read_bytes()).hexdigest()
    assert rec.byte_count == len(FILING) and rec.acquisition_method == METHOD
    assert rec.original_filename == "some-name.htm" and "downloads" not in rec.original_filename  # basename only
    assert rec.imported_at_utc.isoformat() == "2026-05-06T07:08:09+00:00"
    assert rec.operator_supplied_sha256_verified is False and rec.operator_reported_acquired_at is None and rec.claimed_source_url is None
    dumped = json.loads(m.model_dump_json())["external_import"]
    assert not {"http_status", "content_type", "final_url", "retrieved_at_utc", "requested_url"} & set(dumped)
    assert cache_is_valid(tmp_path, m)


def test_operator_supplied_values_are_stored_verbatim_and_flagged_as_unverified_by_name(tmp_path):
    good = hashlib.sha256(FILING).hexdigest().upper()  # case-insensitive comparison
    m, _ = do_import(tmp_path, expected_sha256=good, claimed_source_url="https://example.invalid/x.htm",
                     operator_reported_acquired_at="sometime in March, per operator")
    rec = m.external_import
    assert rec.operator_supplied_sha256_verified is True
    assert rec.claimed_source_url == "https://example.invalid/x.htm"
    assert rec.operator_reported_acquired_at == "sometime in March, per operator"


def test_operator_hash_mismatch_is_refused_and_nothing_is_cached(tmp_path):
    with pytest.raises(CacheMismatch):
        do_import(tmp_path, expected_sha256="00" * 32)
    assert not list(tmp_path.rglob("*.htm.part")) and not cache_path(tmp_path, manifest().target).exists()


@pytest.mark.parametrize("body", [
    b"<html>Undeclared Automated Tool Examplecorp 10-K 2024 " + b"x" * 500 + b"</html>",
    b"<html>Othercorp 10-K 2024 " + b"x" * 500 + b"</html>",
    b"<html>short</html>",
    b"Examplecorp 10-K 2024 plain text " + b"x" * 500,
])
def test_wrong_documents_and_error_pages_are_rejected_offline_too(tmp_path, body):
    with pytest.raises(InvalidFiling):
        do_import(tmp_path, body=body)
    assert not cache_path(tmp_path, manifest().target).exists()


def test_missing_source_file_is_refused(tmp_path):
    with pytest.raises(InvalidFiling):
        import_external_file(manifest(), tmp_path, tmp_path / "nope.htm", acquisition_method=METHOD)


def test_acquisition_method_must_be_described():
    with pytest.raises(ValidationError):
        ExternalImport(acquisition_method="manual", original_filename="a", imported_at_utc=NOW(), cached_filename="a",
                       byte_count=1, sha256="ab" * 32, operator_supplied_sha256_verified=False)


def test_manifest_states_are_mutually_exclusive():
    m, = [do_import.__globals__["manifest"]()]
    rec = ExternalImport(acquisition_method=METHOD, original_filename="a", imported_at_utc=NOW(), cached_filename="a",
                         byte_count=1, sha256="ab" * 32, operator_supplied_sha256_verified=False)
    with pytest.raises(ValidationError):
        m.model_copy(update={"status": "imported_offline"}).model_validate(m.model_copy(update={"status": "imported_offline"}).model_dump())
    with pytest.raises(ValidationError):
        SourceManifest.model_validate({**m.model_dump(), "status": "downloaded", "external_import": rec.model_dump()})
    with pytest.raises(ValidationError):
        SourceManifest.model_validate({**m.model_dump(), "status": "imported_offline"})


def test_cached_import_means_fetch_needs_no_user_agent_and_makes_no_request(tmp_path):
    m, _ = do_import(tmp_path)
    again = fetch_filing(m, tmp_path, env={}, transport=lambda *a: pytest.fail("network used"))
    assert again == m


def test_forced_refresh_replaces_import_record_with_a_retrieval_record(tmp_path):
    m, _ = do_import(tmp_path)
    resp = HttpResponse(200, {"content-type": "text/html"}, FILING, m.target.url)
    out = fetch_filing(m, tmp_path, env={"SEC_USER_AGENT": "unit-test-agent"}, transport=lambda *a: resp, refresh=True,
                       limiter=__import__("finreport.ingest", fromlist=["RateLimiter"]).RateLimiter(0, sleep=lambda s: None),
                       min_bytes=100)
    assert out.status == "downloaded" and out.external_import is None and out.retrieval is not None


def test_cli_import_then_extract_end_to_end(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    assert main(["--root", str(tmp_path), "init"]) == 0
    mpath = tmp_path / "data" / "source_manifest.json"
    m = load_manifest(mpath)
    save_manifest(m.model_copy(update={"target": m.target.model_copy(update={"company": "Examplecorp"})}), mpath)
    src = tmp_path / "elsewhere.htm"
    src.write_bytes(b"<p>Examplecorp FORM 10-K for the fiscal year ended June 30, 2024</p>" + standard_doc())

    assert main(["--root", str(tmp_path), "import-file", str(src), "--method", "short", "--min-bytes", "100"]) == 2  # method too vague
    assert not cache_path(tmp_path, load_manifest(mpath).target).exists()  # refused before anything was written
    assert main(["--root", str(tmp_path), "import-file", str(src), "--method", METHOD, "--sha256", "00" * 32, "--min-bytes", "100"]) == 5
    assert load_manifest(mpath).status == "pending_download"

    assert main(["--root", str(tmp_path), "import-file", str(src), "--method", METHOD, "--min-bytes", "100",
                 "--sha256", hashlib.sha256(src.read_bytes()).hexdigest()]) == 0
    assert load_manifest(mpath).status == "imported_offline"
    assert main(["--root", str(tmp_path), "fetch"]) == 0  # valid cache: no UA, no network
    assert main(["--root", str(tmp_path), "extract"]) == 0
    facts = json.loads((tmp_path / "output" / "fact_table.json").read_text())
    assert len(facts["facts"]) == 6 and facts["source"]["checksum"] == load_manifest(mpath).external_import.sha256

    cache_path(tmp_path, load_manifest(mpath).target).write_bytes(b"tampered")
    assert main(["--root", str(tmp_path), "extract"]) == 5


def test_vague_method_is_refused_before_anything_is_cached(tmp_path):
    with pytest.raises(ValueError, match="acquisition_method"):
        do_import(tmp_path, acquisition_method="manual")
    assert not cache_path(tmp_path, manifest().target).exists()
