"""Targeted rev2 acceptance probes. Synthetic, exposed review cases only."""
# RECONSTRUCTION NOTICE (added by the implementer, not part of the reviewer's text):
# The reviewer's handoff pasted this script with its line breaks and indentation lost and ended partway through the third
# probe (no assignment of the debt-denomination result, no JSON output, no print loop). Structure and indentation below are
# restored from the pasted statements; the tail (third-probe results, JSON output, printing) mirrors the second probe and is
# the implementer's completion. Because of that, this file CANNOT match the reviewer's stated SHA-256
# 8e36343b28213a2e5ec228016ccdf5a93df01ab48f22b02a7c60db22d75396a3 (see RECONSTRUCTION.md). The probe logic, inputs,
# and imports are otherwise exactly as pasted.
import contextlib
import hashlib
import io
import json
import sys
import tempfile
from pathlib import Path

project = Path(sys.argv[1]).resolve()
sys.path[:0] = [str(project / "src"), str(project / "tests"), str(project / "tests/dev")]

from finreport.cli import main
from finreport.evaluation import evaluate
from finreport.extract import extract_facts
from finreport.ingest import cache_path
from finreport.manifest import initial_manifest
from test_review_findings import EXPECTED, numbers_doc, synthetic_microsoft_shaped_root

results = {}

with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    trace = io.StringIO()
    with contextlib.redirect_stdout(trace), contextlib.redirect_stderr(trace):
        synthetic_microsoft_shaped_root(root, numbers_doc())
        extract_before = main(["--root", str(root), "extract"])
        evaluate_before = main(["--root", str(root), "evaluate"])
        cached = cache_path(root, initial_manifest().target)
        before_sha = hashlib.sha256(cached.read_bytes()).hexdigest()
        cached.write_bytes(b"changed cached filing")
        extract_changed = main(["--root", str(root), "extract"])
        evaluate_changed = main(["--root", str(root), "evaluate"])
        changed_report = json.loads((root / "output/evaluation_report.json").read_text())
        cached.unlink()
        evaluate_missing = main(["--root", str(root), "evaluate"])
        missing_report = json.loads((root / "output/evaluation_report.json").read_text())
    results["cache_changed_and_missing"] = {
        "control_extract_exit": extract_before,
        "control_evaluate_exit": evaluate_before,
        "recorded_sha256": before_sha,
        "extract_after_cache_change_exit": extract_changed,
        "evaluate_after_cache_change_exit": evaluate_changed,
        "accepted_after_cache_change": changed_report["acceptance"]["accepted"],
        "evaluate_after_cache_removal_exit": evaluate_missing,
        "accepted_after_cache_removal": missing_report["acceptance"]["accepted"],
        "transcript": trace.getvalue(),
    }

body = numbers_doc()
unrelated_usd = body.replace(b"</body>", b"<p>The USD strengthened against the euro.</p></body>")
sha = hashlib.sha256(unrelated_usd).hexdigest()
table = extract_facts(unrelated_usd, initial_manifest().target, sha)
dump = json.loads(table.model_dump_json())
evaluation = evaluate(EXPECTED, dump, manifest_sha256=sha)
results["unrelated_usd_mention"] = {
    "facts": len(table.facts),
    "currency_evidence_kinds": sorted({f.currency_evidence_kind for f in table.facts}),
    "currency_excerpts": sorted({e.text for f in table.facts for e in f.excerpts if e.kind == "currency"}),
    "accepted": evaluation["acceptance"]["accepted"],
    "reasons": evaluation["acceptance"]["reasons"],
}

instrument = body.replace(b"</body>", b"<p>Our debt is denominated in U.S. dollars.</p></body>")
sha = hashlib.sha256(instrument).hexdigest()
table = extract_facts(instrument, initial_manifest().target, sha)
evaluation = evaluate(EXPECTED, json.loads(table.model_dump_json()), manifest_sha256=sha)
# --- implementer's completion of the truncated tail (mirrors the second probe) ---
results["debt_denomination_mention"] = {
    "facts": len(table.facts),
    "currency_evidence_kinds": sorted({f.currency_evidence_kind for f in table.facts}),
    "currency_excerpts": sorted({e.text for f in table.facts for e in f.excerpts if e.kind == "currency"}),
    "accepted": evaluation["acceptance"]["accepted"],
    "reasons": evaluation["acceptance"]["reasons"],
}

output = Path(sys.argv[2])
output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
for name, result in results.items():
    if name == "cache_changed_and_missing":
        result = {k: v for k, v in result.items() if k != "transcript"}
    print(name, json.dumps(result))
