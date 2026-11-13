import hashlib
import re
from pathlib import Path

from finreport.manifest import load_manifest

ROOT = Path(__file__).parents[2]
SRC = ROOT / "src" / "finreport"


def test_expected_facts_file_is_unmodified():
    recorded = (ROOT / "evaluation" / "expected_facts.sha256").read_text().split()[0]
    assert hashlib.sha256((ROOT / "evaluation" / "expected_facts.json").read_bytes()).hexdigest() == recorded


def test_production_code_does_not_read_or_embed_the_expected_answers():
    forbidden = ["expected_facts", "evaluation/", "245122", "211915", "88136", "72361", "118548", "87582"]
    forbidden += [f"{int(n):,}" for n in ("245122", "211915", "88136", "72361", "118548", "87582")]
    for path in SRC.glob("*.py"):
        if path.name == "evaluation.py":
            continue
        text = path.read_text()
        hits = [f for f in forbidden if f in text]
        # cli.py legitimately names the evaluation file to pass it to the evaluator
        if path.name == "cli.py":
            hits = [h for h in hits if h not in ("expected_facts", "evaluation/")]
        assert not hits, f"{path.name} references {hits}"


def test_extraction_modules_do_not_import_the_evaluator():
    for name in ("extract.py", "parsing.py", "schemas.py", "ingest.py", "manifest.py", "report.py"):
        assert not re.search(r"^\s*(?:from|import)\s+finreport\.evaluation", (SRC / name).read_text(), re.M), name


def test_repo_manifest_is_valid_and_makes_no_unverified_claims():
    m = load_manifest(ROOT / "data" / "source_manifest.json")
    assert m.amendment_search["status"] == "pending"
    assert (m.status == "downloaded") == (m.retrieval is not None)
    assert (m.status == "imported_offline") == (m.external_import is not None)
    if m.recorded_source() is None:
        assert m.status == "pending_download"
        assert not list((ROOT / "data" / "cache").glob("*.htm"))
