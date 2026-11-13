"""Held-out cases. Run explicitly: pytest tests/heldout. See cases.json header for provenance."""
import hashlib
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from finreport.extract import extract_facts
from finreport.manifest import initial_manifest

HERE = Path(__file__).parent
CASES = json.loads((HERE / "cases.json").read_text())["cases"]


@pytest.mark.parametrize("case", CASES, ids=[c["file"] for c in CASES])
def test_heldout_case(case):
    html = (HERE / "fixtures" / case["file"]).read_bytes()
    base = initial_manifest().target
    target = base.model_copy(update={"company": case["company"], "fiscal_period_end": date.fromisoformat(case["fye"])})
    table = extract_facts(html, target, hashlib.sha256(html).hexdigest())

    got = {f.evidence_id: f for f in table.facts}
    want = {f["id"]: f for f in case["facts"]}
    assert set(got) == set(want), f"fact ids differ: extra={set(got) - set(want)} missing={set(want) - set(got)}; issues={[i.code for i in table.issues]}"
    for fid, w in want.items():
        g = got[fid]
        assert g.reported_value == Decimal(w["reported"]), fid
        assert g.scale_multiplier == w["multiplier"] and g.normalized_value == Decimal(w["reported"]) * w["multiplier"], fid
        assert str(g.period.end) == w["end"] and g.scope == "consolidated" and g.currency == "USD", fid
        assert g.locator.printed_page == w["page"], f"{fid} page {g.locator.printed_page}"
    assert sorted((c.kind, c.metric, c.period_label) for c in table.conflicts) == sorted(tuple(c) for c in case["conflicts"])
    assert sorted(i.code for i in table.issues) == sorted(case["issue_codes"]), [i.message for i in table.issues]
    reasons = {e.reason for e in table.exclusions}
    assert set(case["exclusion_reasons"]) <= reasons, reasons
