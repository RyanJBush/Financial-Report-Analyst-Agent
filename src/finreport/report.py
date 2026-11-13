"""Render the fact table and evidence excerpts as Markdown (derived from fact_table.json)."""

from __future__ import annotations

from finreport.schemas import FactTable


def _cell(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def render_fact_table(table: FactTable) -> str:
    s = table.source
    lines = [
        f"# Fact table: {s.company} {s.form}, FYE {s.fiscal_period_end}",
        "",
        f"- Accession: `{s.accession}`",
        f"- Source: {s.url}",
        f"- Source SHA-256: `{s.checksum}`",
        f"- Facts: {len(table.facts)}; conflicts: {len(table.conflicts)}; issues: {len(table.issues)}; exclusions: {len(table.exclusions)}",
        "",
        "| Evidence ID | Metric | Label | Raw | Reported (signed) | Scale | Normalized USD | Currency evidence | Period | Scope | Basis | Statement | Page | Row / Column | table:row:cell | Element ID |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for f in table.facts:
        loc = f.locator
        lines.append("| " + " | ".join(_cell(x) for x in [
            f.evidence_id, f.metric, f.original_label, f.raw_amount_text, f.reported_value, f.scale_multiplier,
            f.normalized_value, f.currency_evidence_kind + (f" [{f.currency_unit_id}={f.currency_unit_measure}]" if f.currency_unit_id else ""),
            f"{f.period.label} ({f.period.start} to {f.period.end})", f.scope, f.accounting_basis,
            loc.statement, loc.printed_page, f"{loc.row} / {loc.column}", f"{loc.table_index}:{loc.row_index}:{loc.cell_index}",
            loc.element_id or "none",
        ]) + " |")
    lines += ["", "## Corroborating occurrences (matching duplicates)", ""]
    for f in table.facts:
        for o in f.corroborating_occurrences:
            lines.append(f"- {f.evidence_id}: {o.statement_title} / {o.original_label} = {o.raw_amount_text} "
                         f"(table {o.locator.table_index}, row {o.locator.row_index})")
    lines += ["", "## Conflicts", ""] + ([f"- {c.kind} {c.metric} {c.period_label}: {c.decision}" for c in table.conflicts] or ["- none"])
    lines += ["", "## Issues", ""] + ([f"- `{i.code}` {i.metric or ''} {i.period_label or ''}: {i.message}" for i in table.issues] or ["- none"])
    lines += ["", "## Exclusions (deliberately not used)", ""]
    lines += [f"- {e.reason}: {e.metric} row {e.row_label!r} (table {e.table_index}, row {e.row_index})" for e in table.exclusions] or ["- none"]
    return "\n".join(lines) + "\n"


def render_excerpts(table: FactTable) -> str:
    lines = ["# Evidence excerpts", "", f"Source SHA-256: `{table.source.checksum}`", ""]
    for f in table.facts:
        lines += [f"## {f.evidence_id}: {f.original_label}, {f.period.label}", ""]
        for e in f.excerpts:
            lines.append(f"- **{e.kind}** ({e.where}): {e.text}")
        lines.append("")
    return "\n".join(lines)
