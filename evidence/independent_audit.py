"""Inspect source HTML directly with BeautifulSoup, without using finreport parsing/extraction."""
import hashlib
import json
import re
import sys
from decimal import Decimal
from pathlib import Path

from bs4 import BeautifulSoup

root = Path(__file__).resolve().parent
source = Path(sys.argv[1])
body = source.read_bytes()
soup = BeautifulSoup(body, "html.parser")
facts = json.loads((root.parent / "output/fact_table.json").read_text(encoding="utf-8"))
expected = json.loads((root.parent / "evaluation/expected_facts.json").read_text(encoding="utf-8"))
gold = {f["evidence_id"]: f for f in expected["facts"]}
top_tables = [t for t in soup.find_all("table") if t.find_parent("table") is None]
concepts = {
    "revenue": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
    "net_income": "us-gaap:NetIncomeLoss",
    "operating_cash_flow": "us-gaap:NetCashProvidedByUsedInOperatingActivities",
}

def clean(tag):
    return re.sub(r"\s+", " ", tag.get_text("", strip=False).replace("\xa0", " ")).strip()

rows = []
for fact in facts["facts"]:
    loc = fact["locator"]
    table = top_tables[loc["table_index"]]
    source_rows = table.find_all("tr")
    row = source_rows[loc["row_index"]]
    cells = row.find_all(["td", "th"], recursive=False)
    cell = cells[loc["cell_index"]]
    header = source_rows[loc["header_row_index"]].find_all(["td", "th"], recursive=False)[loc["header_cell_index"]]
    ix = cell.find("ix:nonfraction")
    assert ix is not None
    unit_id = ix.get("unitref")
    context_id = ix.get("contextref")
    units = soup.find_all("xbrli:unit", attrs={"id": unit_id})
    contexts = soup.find_all("xbrli:context", attrs={"id": context_id})
    assert len(units) == len(contexts) == 1
    unit, context = units[0], contexts[0]
    measure = unit.find("xbrli:measure").get_text(strip=True)
    start = context.find("xbrli:startdate").get_text(strip=True)
    end = context.find("xbrli:enddate").get_text(strip=True)
    identifier = context.find("xbrli:identifier").get_text(strip=True)
    raw = clean(cell)
    signed_value = Decimal(raw.replace(",", "").replace("$", ""))
    scale = int(ix.get("scale", "0"))
    if ix.get("sign") == "-":
        signed_value = -signed_value
    exp = gold[fact["evidence_id"]]
    dims = context.find_all(["xbrldi:explicitmember", "xbrldi:typedmember"])
    next_hr = table.find_next("hr")
    footer = next_hr.find_previous("p") if next_hr else None
    footer_text = clean(footer) if footer else None
    checks = {
        "source_checksum": hashlib.sha256(body).hexdigest() == fact["source_checksum"],
        "row_label": clean(cells[0]) == exp["original_label"],
        "year_column": clean(header) == exp["locator"]["column"],
        "signed_reported_value": signed_value == Decimal(exp["reported_value"]),
        "source_scale": scale == 6,
        "normalized_value": signed_value * (Decimal(10) ** scale) == Decimal(exp["normalized_value_usd"]),
        "currency_measure": measure == "iso4217:USD" and unit.find("xbrli:divide") is None,
        "currency_namespace": soup.html.get("xmlns:iso4217") == "http://www.xbrl.org/2003/iso4217",
        "context_period": start == fact["period"]["start"] and end == fact["period"]["end"],
        "context_entity": int(identifier) == 789019,
        "context_no_dimensions": len(dims) == 0,
        "source_concept": ix.get("name") == concepts[fact["metric"]],
        "printed_page_footer": footer_text == str(exp["locator"]["printed_page"]),
    }
    rows.append({
        "evidence_id": fact["evidence_id"], "raw_cell": raw, "row_label": clean(cells[0]),
        "year_header": clean(header), "inline_fact_id": ix.get("id"), "concept": ix.get("name"),
        "unit_ref": unit_id, "unit_measure": measure, "context_ref": context_id,
        "period_start": start, "period_end": end, "entity_identifier": identifier,
        "explicit_or_typed_dimensions": len(dims), "printed_page_footer": footer_text,
        "checks": checks, "passed": all(checks.values()),
    })

metadata = {t.get("name"): t.get_text("", strip=True) for t in soup.find_all("ix:nonnumeric")
            if t.get("name") in {"dei:EntityRegistrantName", "dei:DocumentType", "dei:DocumentPeriodEndDate", "dei:DocumentFiscalYearFocus"}}
prose = []
for p in soup.find_all("p"):
    text = clean(p)
    if ("consolidated financial statements include the accounts" in text.lower()
            or "in accordance with accounting principles generally accepted" in text.lower()
            or "recast certain prior period amounts" in text.lower()):
        prose.append(text)
scripts = re.findall(rb"<script\b[^>]*>.*?</script>|<noscript\b[^>]*>.*?</noscript>", body, flags=re.I | re.S)
out = {"method": "Direct source-HTML inspection with BeautifulSoup; no finreport parsing/extraction functions imported.",
       "source_sha256": hashlib.sha256(body).hexdigest(), "source_bytes": len(body),
       "document_metadata": metadata, "scope_basis_and_recast_prose": prose,
       "script_and_noscript_element_byte_lengths": [len(s) for s in scripts],
       "script_and_noscript_total_bytes": sum(map(len, scripts)),
       "facts_reviewed": len(rows), "facts_passing_all_direct_source_checks": sum(r["passed"] for r in rows),
       "facts": rows}
(root / "independent_source_audit.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
print(json.dumps(out, indent=2))
if not rows or not all(row['passed'] for row in rows):
    raise SystemExit(1)
