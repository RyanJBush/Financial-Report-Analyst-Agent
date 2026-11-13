"""Command line: init | fetch | import-file | extract | evaluate.

Exit codes: 0 ok (for evaluate: ACCEPTED by machine checks); 1 evaluation rejected; 2 missing input; 3 SEC_USER_AGENT missing;
4 access denied or response rejected as not-the-filing; 5 checksum/cache mismatch;
6 extraction produced issues or conflicts; 7 network failure after bounded retries.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

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
    from finreport.answers import answer_question, load_verified_table, validate_answer
    from finreport.narrative import ollama, render, scripted, validate_narrative
    try:
        table = load_verified_table(root)
        answer = answer_question(args.question,table)
        if not validate_answer(answer,table):
            raise ValueError('answer validation failed')
        # Unsupported questions never need a model request.
        narrative = (ollama(answer,args.model) if args.adapter == 'ollama' and answer['status'] == 'answered'
                     else scripted(answer))
        if not validate_narrative(narrative,answer):
            raise ValueError('narrative validation failed')
        result = {'answer':answer,'narrative':narrative,'summary':render(answer,narrative)}
        facts = [f for f in table.facts if f.evidence_id in answer['evidence_ids']]
        result['supporting_facts'] = [f.model_dump(mode='json') for f in facts]
        if args.json:
            print(json.dumps(result,indent=2))
        else:
            print(f"Question: {args.question}\nAdapter: {narrative['adapter']} (model: {narrative['model'] or 'none; deterministic'})")
            print(result['summary'])
            print(answer['scope_note'])
            if facts:
                print('\nEvidence ID | Label | Period | Reported value | Scale | Currency | Scope | Page')
            for f in facts:
                print(f'{f.evidence_id} | {f.original_label} | {f.period.label} | {f.reported_value} | {f.scale_multiplier} | {f.currency} | {f.scope} | {f.locator.printed_page}')
                print(f'Source: {f.source_url}#{f.locator.element_id or ""}')
                for e in f.excerpts:
                    print(f'  {e.kind} ({e.where}): {e.text}')
            if answer['calculation']:
                print('\nCalculation: '+json.dumps(answer['calculation']))
        return 0 if answer['status'] == 'answered' else 1
    except (OSError,ValueError,KeyError,TypeError) as err:
        # No silent scripted fallback when a requested model fails.
        print(json.dumps({'status':'unresolved','reason':'input_or_adapter_failure','detail':str(err)}),file=sys.stderr)
        return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="finreport", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="project root (default: current directory)")
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
    ask = sub.add_parser('ask',help='answer a bounded historical question with verified evidence')
    ask.add_argument('question')
    ask.add_argument('--adapter',choices=['scripted','ollama'],default='scripted')
    ask.add_argument('--model',default='',help='exact installed Ollama model name; no auto-download')
    ask.add_argument('--json',action='store_true',help='emit structured answer and complete cited facts')
    args = parser.parse_args(argv)
    handler = {"init": cmd_init, "fetch": cmd_fetch, "import-file": cmd_import, "extract": cmd_extract, "evaluate": cmd_evaluate,'ask':cmd_ask}[args.command]
    return handler(args.root.resolve(), args)


if __name__ == "__main__":
    raise SystemExit(main())
