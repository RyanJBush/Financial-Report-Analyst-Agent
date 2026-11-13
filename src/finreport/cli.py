"""Command line: init | fetch | import-file | extract | evaluate | ask | answer | demo | live | eval-answers.

Exit codes: 0 ok (for evaluate: ACCEPTED by machine checks); 1 evaluation rejected; 2 missing input; 3 SEC_USER_AGENT missing;
4 access denied or response rejected as not-the-filing; 5 checksum/cache mismatch;
6 extraction produced issues or conflicts; 7 network failure after bounded retries (for `live`: model provider unavailable);
8 `live`: the model's narrative was rejected by validation (the structured answer is still printed).
`ask`/`answer`/`live`: 0 answered, 1 unresolved, 5 refused, 2 no usable fact table. `demo`: 0 only if all three demo questions behave as specified.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from finreport.answer_eval import evaluate_answers, render_report
from finreport.answers import FactBookError, _load_snapshot, answer_question, answer_to_json, load_factbook
from finreport.display import render
from finreport.narrative import AnthropicApiAdapter, ClaudeCliAdapter, ScriptedAdapter, run_narrative
from finreport.evaluation import evaluate_files
from finreport.extract import extract_facts
from finreport.ingest import DEFAULT_MIN_BYTES, IngestError, cache_path, fetch_filing, import_external_file, verify_cached_source
from finreport.manifest import initial_manifest, load_manifest, save_manifest
from finreport.report import render_excerpts, render_fact_table


def _paths(root: Path) -> dict[str, Path]:
    return {
        "manifest": root / "data" / "source_manifest.json",
        "expected": root / "evaluation" / "expected_facts.json",
        "facts": root / "output" / "fact_table.json",
        "table_md": root / "output" / "fact_table.md",
        "excerpts_md": root / "output" / "evidence_excerpts.md",
        "report": root / "output" / "evaluation_report.json",
    }


def cmd_init(root: Path, _args: argparse.Namespace) -> int:
    p = _paths(root)
    if p["manifest"].exists():
        print(f"manifest already exists: {p['manifest']}")
        return 0
    save_manifest(initial_manifest(), p["manifest"])
    print(f"wrote {p['manifest']} (status: pending_download)")
    return 0


def cmd_fetch(root: Path, args: argparse.Namespace) -> int:
    p = _paths(root)
    if not p["manifest"].exists():
        print("no manifest; run `finreport init` first", file=sys.stderr)
        return 2
    manifest = load_manifest(p["manifest"])
    try:
        updated = fetch_filing(manifest, root, refresh=args.refresh)
    except IngestError as err:
        print(f"fetch stopped: {err}", file=sys.stderr)
        return err.exit_code
    save_manifest(updated, p["manifest"])
    recorded = updated.recorded_source()
    assert recorded is not None
    how = "downloaded" if updated.retrieval else "imported offline (no download performed)"
    print(f"cached {recorded[0]} ({how}): {recorded[1]} bytes, sha256 {recorded[2]}")
    return 0


def cmd_import(root: Path, args: argparse.Namespace) -> int:
    p = _paths(root)
    if not p["manifest"].exists():
        print("no manifest; run `finreport init` first", file=sys.stderr)
        return 2
    manifest = load_manifest(p["manifest"])
    try:
        updated = import_external_file(
            manifest, root, args.file, acquisition_method=args.method, claimed_source_url=args.claimed_url,
            operator_reported_acquired_at=args.acquired_at, expected_sha256=args.sha256, min_bytes=args.min_bytes)
    except IngestError as err:
        print(f"import stopped: {err}", file=sys.stderr)
        return err.exit_code
    except ValueError as err:  # pydantic validation, e.g. --method too short
        print(f"import stopped: invalid import record: {err}", file=sys.stderr)
        return 2
    save_manifest(updated, p["manifest"])
    r = updated.external_import
    assert r is not None
    print(f"imported {r.original_filename} as {r.cached_filename}: {r.byte_count} bytes, sha256 {r.sha256}")
    return 0


def cmd_extract(root: Path, _args: argparse.Namespace) -> int:
    p = _paths(root)
    if not p["manifest"].exists():
        print("no manifest; run `finreport init` first", file=sys.stderr)
        return 2
    manifest = load_manifest(p["manifest"])
    recorded = manifest.recorded_source()
    if recorded is None:
        print("source not downloaded or imported (manifest status: pending_download); nothing to extract", file=sys.stderr)
        return 2
    path = cache_path(root, manifest.target)
    sv = verify_cached_source(root, manifest)
    if "cached_file_missing" in sv["problems"] or "cached_file_is_symlink" in sv["problems"]:
        print(f"cached file missing or not a regular file: {path}", file=sys.stderr)
        return 2
    if not sv["verified"]:
        print(f"cached source does not match the manifest ({', '.join(sv['problems'])}): recorded {sv['recorded']}, actual {sv['actual']}", file=sys.stderr)
        return 5
    checksum = sv["actual"]["sha256"]
    table = extract_facts(path.read_bytes(), manifest.target, checksum)
    p["facts"].parent.mkdir(parents=True, exist_ok=True)
    p["facts"].write_text(json.dumps(json.loads(table.model_dump_json()), indent=2) + "\n", encoding="utf-8")
    p["table_md"].write_text(render_fact_table(table), encoding="utf-8")
    p["excerpts_md"].write_text(render_excerpts(table), encoding="utf-8")
    print(f"facts={len(table.facts)} conflicts={len(table.conflicts)} issues={len(table.issues)} "
          f"exclusions={len(table.exclusions)} -> {p['facts']}")
    unresolved = [c for c in table.conflicts if not c.resolved]
    return 6 if (table.issues or unresolved) else 0


def cmd_evaluate(root: Path, _args: argparse.Namespace) -> int:
    p = _paths(root)
    for key in ("expected", "facts"):
        if not p[key].is_file():
            print(f"missing {p[key]}", file=sys.stderr)
            return 2
    manifest_sha, source_check = None, {"verified": False, "problems": ["no_manifest"], "recorded": None, "actual": None}
    if p["manifest"].exists():
        manifest = load_manifest(p["manifest"])
        recorded = manifest.recorded_source()
        manifest_sha = recorded[2] if recorded else None
        source_check = verify_cached_source(root, manifest)  # re-verified NOW, from the bytes currently on disk
    try:
        result = evaluate_files(p["expected"], p["facts"], manifest_sha256=manifest_sha, source_check=source_check)
    except json.JSONDecodeError as err:
        print(f"cannot read {p['facts'].name} or the expectations as JSON: {err}", file=sys.stderr)
        return 2
    p["report"].write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    acc = result["acceptance"]
    print(f"numeric diagnostic (NOT acceptance): {result['numeric_fact_correctness']}; extra facts: {result['unexpected_extra_facts']}; "
          f"unresolved conflicts: {result['unresolved_conflicts']}; blocking issues: {result['blocking_issues']}")
    sv = result["source_verification"] or {}
    print(f"source verification: {'OK' if sv.get('verified') else 'FAILED (' + ', '.join(sv.get('problems', [])) + ')'}")
    print(f"acceptance: {acc['decision'].upper()}" + ("" if acc["accepted"] else f" ({', '.join(r['code'] for r in acc['reasons'])})"))
    print(f"report -> {p['report']}")
    return 0 if acc["accepted"] else 1


def cmd_ask(root: Path, args: argparse.Namespace) -> int:
    """Answer one question from the verified fact table. Prints the structured answer as JSON.

    Exit: 0 answered; 1 unresolved (not answerable from this evidence); 5 refused (evidence not verified); 2 no usable fact table.
    This command never reads the evaluation expectations."""
    try:
        book = load_factbook(root)
    except FactBookError as err:
        print(f"ask stopped: {err}", file=sys.stderr)
        return 2
    answer = answer_question(args.question, book)
    text = answer_to_json(answer)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(text, end="")
    return {"answered": 0, "unresolved": 1, "refused": 5}[answer.status]


DEMO_QUESTIONS = (
    ("lookup", "What was Microsoft's consolidated revenue in FY2024?", "answered"),
    ("change", "How much did consolidated revenue change from FY2023 to FY2024?", "answered"),
    ("unsupported forecast", "What will Microsoft's consolidated revenue be in FY2027?", "unresolved"),
)
EXIT_BY_STATUS = {"answered": 0, "unresolved": 1, "refused": 5}


def _answer_root(root: Path | None) -> Path:
    """Explicit --root wins. Otherwise the current directory if it holds a fact table, else the bundled example under it."""
    if root is not None:
        return root.resolve()
    cwd = Path.cwd()
    return cwd if (cwd / "output" / "fact_table.json").exists() else cwd / "examples" / "msft_fy2024"


def _load_book(root: Path):
    # snapshot mode is allowed here only: it is used when the filing bytes are absent, and the answer says so.
    return load_factbook(root, allow_snapshot=True)


def cmd_answer(root: Path | None, args: argparse.Namespace) -> int:
    """Answer one question and add a SCRIPTED (deterministic, offline) narrative. Not a model."""
    r = _answer_root(root)
    try:
        book = _load_book(r)
    except FactBookError as err:
        print(f"answer stopped: {err}", file=sys.stderr)
        return 2
    answer = answer_question(args.question, book)
    narrative = run_narrative(answer, ScriptedAdapter())
    if args.json:
        print(json.dumps({"answer": json.loads(answer_to_json(answer)), "narrative": json.loads(narrative.model_dump_json())}, indent=2))
    else:
        print(render(answer, narrative), end="")
    return EXIT_BY_STATUS[answer.status]


def cmd_demo(root: Path | None, _args: argparse.Namespace) -> int:
    """Deterministic offline demo of the three required questions. No network, no model, no credential."""
    r = _answer_root(root)
    try:
        book = _load_book(r)
    except FactBookError as err:
        print(f"demo stopped: {err}", file=sys.stderr)
        return 2
    ok = True
    for i, (kind, question, expected) in enumerate(DEMO_QUESTIONS, 1):
        answer = answer_question(question, book)
        narrative = run_narrative(answer, ScriptedAdapter())
        print(f"===== Demo question {i} of {len(DEMO_QUESTIONS)}: {kind} =====")
        print(render(answer, narrative))
        if answer.status != expected or (answer.status == "answered" and narrative.status != "validated"):
            ok = False
            print(f"DEMO CHECK FAILED: expected status {expected}, got {answer.status} (narrative {narrative.status})", file=sys.stderr)
    print("demo result:", "all three questions behaved as specified" if ok else "UNEXPECTED RESULT")
    return 0 if ok else 1


def _provider(args: argparse.Namespace):
    if args.provider == "claude-cli":
        return ClaudeCliAdapter(model=args.model)
    return AnthropicApiAdapter(model=args.model)


def cmd_live(root: Path | None, args: argparse.Namespace) -> int:
    """Answer one question, then ask a REAL model to restate the verified answer; the output is accepted only if it passes validation."""
    r = _answer_root(root)
    try:
        book = _load_book(r)
    except FactBookError as err:
        print(f"live stopped: {err}", file=sys.stderr)
        return 2
    answer = answer_question(args.question, book)
    adapter = _provider(args)
    narrative = run_narrative(answer, adapter, max_attempts=args.max_attempts, max_output_tokens=args.max_output_tokens, timeout_seconds=args.timeout)
    print(render(answer, narrative), end="")
    if args.record:
        _write_run_record(args.record, answer_to_json(answer), narrative)
    if narrative.status == "unavailable":
        return 7
    if narrative.status == "rejected":
        return 8
    return EXIT_BY_STATUS[answer.status]


def _write_run_record(directory: Path, answer_json: str, narrative) -> Path:
    """Persist what a live run actually did. Contains no credential, environment variable or prompt secret; the prompt is hashed."""
    directory.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    record = {"record_version": 1, "recorded_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "answer_sha256": hashlib.sha256(answer_json.encode()).hexdigest(),
              "answer": json.loads(answer_json), "narrative": json.loads(narrative.model_dump_json())}
    path = directory / f"{now.strftime('%Y%m%dT%H%M%SZ')}-{narrative.adapter}-{narrative.status}-{hashlib.sha256(answer_json.encode()).hexdigest()[:8]}.json"
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(f"run record written: {path}", file=sys.stderr)
    return path


def cmd_eval_answers(root: Path | None, args: argparse.Namespace) -> int:
    """Run the frozen answer cases (evaluation/answer_cases.json under --cases-root) against the fact table under --root.
    Exit 0 if every case passes, 1 if any fails, 5 if the case file differs from its freeze record, 2 if inputs are missing."""
    facts_root = _answer_root(root)
    cases_root = (args.cases_root or Path.cwd()).resolve()
    try:
        book = load_factbook(facts_root, allow_snapshot=True)
        report = evaluate_answers(cases_root, book, _load_snapshot(facts_root))
    except (FactBookError, FileNotFoundError) as err:
        print(f"eval-answers stopped: {err}", file=sys.stderr)
        return 2
    if args.out:
        args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(render_report(report), end="")
    if not report["cases"]["matches_freeze_record"]:
        return 5
    return 0 if not report["failures"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="finreport", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=None, help="project root (default: current directory; answer/demo/live fall back to examples/msft_fy2024)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="write the pending source manifest")
    fetch = sub.add_parser("fetch", help="download and cache the filing (needs SEC_USER_AGENT)")
    fetch.add_argument("--refresh", action="store_true", help="ignore a valid cache and download again")
    imp = sub.add_parser("import-file", help="offline: validate, hash and cache a filing downloaded outside this tool")
    imp.add_argument("file", type=Path, help="path to the externally downloaded HTML file")
    imp.add_argument("--method", required=True, help="how the file was actually obtained (free text, required, min 10 chars)")
    imp.add_argument("--claimed-url", default=None, help="URL the file was reportedly downloaded from (unverified)")
    imp.add_argument("--acquired-at", default=None, help="when the operator says it was downloaded (free text, unverified)")
    imp.add_argument("--sha256", default=None, help="operator-computed SHA-256 to verify against")
    imp.add_argument("--min-bytes", type=int, default=DEFAULT_MIN_BYTES, help=argparse.SUPPRESS)
    sub.add_parser("extract", help="extract facts from the cached filing")
    sub.add_parser("evaluate", help="compare output/fact_table.json to evaluation/expected_facts.json")
    ask = sub.add_parser("ask", help="answer one supported question from the verified fact table (deterministic; no model)")
    ask.add_argument("question")
    ask.add_argument("--out", type=Path, default=None, help="also write the JSON answer to this file")
    ans = sub.add_parser("answer", help="answer a question with a SCRIPTED narrative (offline, deterministic; not a model)")
    ans.add_argument("question")
    ans.add_argument("--json", action="store_true", help="print the answer and narrative as JSON")
    sub.add_parser("demo", help="offline demo: the three required questions, deterministic, no network, no model")
    live = sub.add_parser("live", help="answer a question and have a REAL model restate it; output is validated (needs a provider)")
    live.add_argument("question")
    live.add_argument("--provider", choices=("claude-cli", "anthropic-api"), default="claude-cli")
    live.add_argument("--model", default=None, help="model id (provider default if omitted)")
    live.add_argument("--max-attempts", type=int, default=2, help="validated retries, 1..3 (default 2)")
    live.add_argument("--max-output-tokens", type=int, default=500)
    live.add_argument("--timeout", type=float, default=90.0, help="seconds per provider call")
    live.add_argument("--record", type=Path, default=None, help="directory to write a JSON run record into")
    ev = sub.add_parser("eval-answers", help="run the frozen answer-evaluation cases; dimensions reported separately")
    ev.add_argument("--cases-root", type=Path, default=None, help="directory holding evaluation/answer_cases.json (default: current directory)")
    ev.add_argument("--out", type=Path, default=None, help="write the JSON report here")
    args = parser.parse_args(argv)
    if args.command == "live":
        args.max_attempts = max(1, min(3, args.max_attempts))
    if args.command in ("answer", "demo", "live", "eval-answers"):
        return {"answer": cmd_answer, "demo": cmd_demo, "live": cmd_live, "eval-answers": cmd_eval_answers}[args.command](args.root, args)
    root = (args.root or Path.cwd())
    handler = {"init": cmd_init, "fetch": cmd_fetch, "import-file": cmd_import, "extract": cmd_extract, "evaluate": cmd_evaluate, "ask": cmd_ask}[args.command]
    return handler(root.resolve(), args)


if __name__ == "__main__":
    raise SystemExit(main())
