#!/usr/bin/env python3
"""Run every mutation in review/mutations/mutations.json against a scratch copy of src/.

For each mutation: copy src, apply the single replacement (must match exactly once), confirm the
interpreter imports the scratch copy, run the dev suite and the regression suite WITHOUT early stop,
and record which tests failed. A mutation is DETECTED if any test fails or errors; SURVIVED if all pass.
Also runs a control (no mutation; must pass) and the history demo for the one original survivor.

Usage: python run_mutations.py [--project DIR] [--out DIR]
"""
import argparse, json, os, re, shutil, subprocess, sys, tempfile
from pathlib import Path

ISOLATING = "tests/dev/test_ingest.py::test_error_page_signature_alone_is_enough_to_reject"


def pytest(proj, src, extra):
    env = dict(os.environ, PYTHONPATH=str(src), PYTHONDONTWRITEBYTECODE="1")
    probe = subprocess.run([sys.executable, "-c", "import finreport;print(finreport.__file__)"], env=env, capture_output=True, text=True).stdout.strip()
    cmd = [sys.executable, "-m", "pytest", "-q", "--tb=no", "-p", "no:cacheprovider", *extra]
    r = subprocess.run(cmd, cwd=proj, env=env, capture_output=True, text=True)
    failed = sorted(set(re.findall(r"^(?:FAILED|ERROR) (\S+)", r.stdout, re.M)))
    summary = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    return dict(exit=r.returncode, summary=summary, failed=failed, imported_from=probe, cmd=" ".join(cmd))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--spec", type=Path, default=None, help="mutation spec JSON (default review/mutations/mutations.json)")
    a = ap.parse_args()
    proj = a.project.resolve()
    out = (a.out or proj / "review" / "mutations").resolve()
    spec = json.loads((a.spec or proj / "review/mutations/mutations.json").read_text())
    out.mkdir(parents=True, exist_ok=True)
    results = []
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        def scratch(name, mut=None):
            d = t / name
            shutil.copytree(proj / "src", d, ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"))
            if mut:
                f = d.parent.joinpath(name, *Path(mut["file"]).parts[1:])  # strip leading 'src'
                text = f.read_text()
                assert text.count(mut["old"]) == 1, mut["name"]
                f.write_text(text.replace(mut["old"], mut["new"]))
            return d
        ctl_src = scratch("control")
        control = dict(dev=pytest(proj, ctl_src, ["tests/dev"]), regression=pytest(proj, ctl_src, ["tests/heldout"]))
        for m in spec:
            src = scratch(f"m{m['id']}", m)
            dev, reg = pytest(proj, src, ["tests/dev"]), pytest(proj, src, ["tests/heldout"])
            used = dev["imported_from"].startswith(str(src))
            results.append(dict(id=m["id"], name=m["name"], file=m["file"], intent=m["intent"], imported_mutated_copy=used,
                                detected=(dev["exit"] != 0 or reg["exit"] != 0), dev=dev, regression=reg,
                                original_observation=m["original_observation"]))
        demo = None
        demo_m = next((m for m in spec if m["name"] == "no-error-page-check"), None)
        if demo_m is not None:
            demo_m = next(m for m in spec if m["name"] == "no-error-page-check")
            demo_src = scratch("demo", demo_m)
            demo = pytest(proj, demo_src, ["tests/dev", "--ignore=tests/dev/test_offline_import.py", f"--deselect={ISOLATING}"])
            demo["note"] = ("no-error-page-check against the dev suite minus the isolating test and minus tests/dev/test_offline_import.py "
                            "(approximation of the original 100-test suite). Exit 0 = mutation survives.")
    doc = dict(control=control, results=results, history_demo=demo,
               detected=sum(r["detected"] for r in results), total=len(results))
    (out / "results.json").write_text(json.dumps(doc, indent=2) + "\n")
    lines = ["# Mutation results (re-run against the current tree)", "",
             f"Control (unmutated): dev `{control['dev']['summary']}`; regression `{control['regression']['summary']}`.", "",
             f"Detected: **{doc['detected']}/{doc['total']}**. Every run imported the mutated scratch copy: "
             f"{all(r['imported_mutated_copy'] for r in results)}.", "",
             "| # | mutation | detected | dev failures | regression failures |", "|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['id']} | {r['name']} | {'yes' if r['detected'] else '**NO**'} | {len(r['dev']['failed'])} | {len(r['regression']['failed'])} |")
    if demo is not None:
        lines += ["", f"History demo (original survivor): exit {demo['exit']} -> {'SURVIVED' if demo['exit'] == 0 else 'detected'}; {demo['summary']}", ""]
    (out / "results.md").write_text("\n".join(lines))
    print("\n".join(lines))
    return 0 if control["dev"]["exit"] == 0 and doc["detected"] == doc["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
