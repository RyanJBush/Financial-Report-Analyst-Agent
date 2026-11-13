"""Plain-text rendering of an answer (and optional narrative) for the CLI. Rendering only: no decisions are made here."""

from __future__ import annotations

from finreport.answers import ChangeValue, LookupValue, Answer, METRIC_LABELS
from finreport.narrative import NarrativeResult

LEVEL_TEXT = {
    "cached_source_verified": "cached filing bytes re-hashed and matched the manifest (full verification)",
    "reviewed_snapshot_fact_table_only": "WEAKER: filing bytes not present; fact table matches the recorded hash of the reviewed snapshot",
    "unverified": "NOT VERIFIED",
}


def concise_answer(a: Answer) -> str:
    """One deterministic sentence built only from the structured value (never from model output)."""
    if a.status != "answered" or a.value is None:
        return f"No answer: {a.reason}"
    v = a.value
    label = METRIC_LABELS.get(v.metric, v.metric)
    if isinstance(v, LookupValue):
        return f"{a.entity}'s consolidated {label} for {v.period_label} was {v.display}."
    assert isinstance(v, ChangeValue)
    span = f"from {v.prior_period_label} to {v.current_period_label}"
    if v.direction == "no_change":
        return f"{a.entity}'s consolidated {label} was unchanged {span} (change of {v.absolute_change_display.lstrip('-')})."
    pct = f" ({v.percent_change_display.lstrip('-')})" if v.percent_change_display else ""
    suffix = "" if v.percent_change_display else f" No percentage is reported: {v.percent_withheld_reason}"
    word = "increased" if v.direction == "increase" else "decreased"
    return f"{a.entity}'s consolidated {label} {word} by {v.absolute_change_display.lstrip('-')}{pct} {span}.{suffix}"


def _wrap(text: str, indent: str, width: int = 100) -> str:
    hang = " " * len(indent)
    words, lines, cur = text.split(), [], indent
    for w in words:
        if len(cur) + len(w) + 1 > width and cur.strip():
            lines.append(cur.rstrip())
            cur = hang
        cur += w + " "
    lines.append(cur.rstrip())
    return "\n".join(lines)


def render(a: Answer, narrative: NarrativeResult | None = None) -> str:
    out = [f"Question : {a.question}", f"Status   : {a.status}" + (f" ({a.reason_code})" if a.reason_code else ""),
           f"Answer   : {concise_answer(a)}"]
    if a.status == "answered":
        out.append("")
        out.append("Facts used")
        for e in a.evidence:
            page = f", printed page {e.locator.printed_page}" if e.locator.printed_page else ""
            out.append(f"  [{e.evidence_id}] {e.original_label} | {e.period_label} | {e.raw_amount_text} (x{e.scale_multiplier:,}) = {e.normalized_value:,} {e.currency}")
            out.append(f"      {e.locator.statement}{page}; table {e.locator.table_index}, row {e.locator.row_index}, cell {e.locator.cell_index}; "
                       f"scope={e.scope}; basis={e.accounting_basis}; currency evidence={e.currency_evidence_kind}")
            out.append(f"      row text: {e.amount_excerpt[:160]}")
            for x in e.supporting_excerpts:
                out.append(f"      {x.kind}: {x.text[:140]}")
        if a.calculation is not None:
            out += ["", "Calculation (Decimal; unrounded intermediate values)"]
            out += [f"  {s}" for s in a.calculation.steps]
    out += ["", f"Verification: {LEVEL_TEXT.get(a.source.verification_level, a.source.verification_level)}",
            f"Source      : {a.source.company} {a.source.form}, accession {a.source.accession}, source checksum {a.source.checksum[:16]}..."]
    if a.source.verification_problems:
        out.append(f"Problems    : {', '.join(a.source.verification_problems)}")
    if narrative is not None:
        out.append("")
        out.append(_narrative_block(narrative))
    out += ["", "Limitations"] + [_wrap(x, "  - ", 100) for x in a.limitations]
    return "\n".join(out) + "\n"


def _narrative_block(n: NarrativeResult) -> str:
    if n.adapter == "scripted":
        label = "Narrative (SCRIPTED TEMPLATE - not a model)"
    else:
        label = f"Narrative (LIVE MODEL {n.model or 'unknown'} via {n.adapter}; validated by deterministic checks)"
    if n.status == "validated":
        cites = "; ".join(f"[{', '.join(c.evidence_ids + ([c.calculation_id] if c.calculation_id else []))}]" for c in n.claims)
        return f"{label}\n{_wrap(n.text, '  ')}\n  cited: {cites}\n  attempts: {n.attempts}"
    if n.status == "not_generated":
        return "Narrative: none generated (there is no verified value to restate; the reason above is the deterministic explanation)."
    if n.status == "unavailable":
        return f"Narrative: NOT GENERATED - provider unavailable ({n.adapter}): {n.unavailable_reason}\n  The structured answer above is unaffected."
    codes = ", ".join(sorted({v.code for v in n.violations})) or "unknown"
    return (f"Narrative: REJECTED by validation after {n.attempts} attempt(s) ({n.adapter}, {n.model}): {codes}\n"
            "  No model text is shown. The structured answer above is unaffected.")
