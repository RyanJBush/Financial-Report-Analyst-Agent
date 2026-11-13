"""Extract source-linked facts from parsed filing HTML.

This module must never read or reference the evaluation expectations; a test enforces it.
Inputs are only: the HTML bytes, its checksum, and the selected-filing metadata (Target).

Decision rules, in order:
  1. A row is a candidate only if its table is a recognised primary statement (title found in the
     headings just above the table), is not a segment or non-GAAP table, and the statement kind is
     allowed for the metric.
  2. Period columns come from the table's own year header and its "Year Ended <date>" wording, never
     from column position. Only the target fiscal year and the one before it are extracted.
  3. Scale comes from a "(In millions)"-style statement; absent or contradictory scale blocks the fact.
  4. Scope (consolidated) and accounting basis (GAAP) need document-level evidence text; absent -> issue.
  5. Candidates are grouped by (metric, period end, scope, basis). Within a group, equal normalized
     values are corroborating duplicates; unequal values among authoritative-statement occurrences
     are an unresolved conflict and NO fact is emitted; a disagreeing non-authoritative occurrence is
     recorded as a conflict but the authoritative value is kept and flagged.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from finreport.manifest import Target
from finreport.parsing import (
    Document,
    LogicalCell,
    Row,
    Table,
    AmountError,
    is_dash,
    looks_like_amount,
    parse_amount,
    parse_document,
)
from finreport.schemas import (
    Conflict,
    Exclusion,
    Excerpt,
    Fact,
    FactTable,
    Issue,
    Locator,
    Occurrence,
    Period,
    SourceRef,
)

# ---------------------------------------------------------------- metric definitions


@dataclass(frozen=True)
class MetricSpec:
    key: str
    code: str
    labels: tuple[str, ...]
    authoritative: str
    corroborating: tuple[str, ...] = ()


METRICS: tuple[MetricSpec, ...] = (
    MetricSpec(
        "revenue", "REV",
        ("total revenue", "total revenues", "revenue", "revenues", "total net revenue", "total net revenues",
         "net revenue", "net revenues", "net sales", "total net sales"),
        authoritative="income",
    ),
    MetricSpec(
        "net_income", "NI",
        ("net income", "net income (loss)", "net earnings", "net earnings (loss)"),
        authoritative="income",
        corroborating=("cash_flow", "comprehensive_income"),
    ),
    MetricSpec(
        "operating_cash_flow", "OCF",
        ("net cash from operations", "net cash from operating activities", "net cash provided by operating activities",
         "net cash provided by (used in) operating activities", "net cash provided by operations",
         "net cash (used in) provided by operating activities"),
        authoritative="cash_flow",
    ),
)
_LABEL_INDEX = {label: spec for spec in METRICS for label in spec.labels}

# Order matters: comprehensive-income titles also contain "income statements".
STATEMENT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("comprehensive_income", re.compile(r"comprehensive\s+(?:income|loss|earnings)", re.I)),
    ("cash_flow", re.compile(r"cash\s+flows?\s+statements?|statements?\s+of\s+(?:consolidated\s+)?cash\s+flows?", re.I)),
    ("balance_sheet", re.compile(r"balance\s+sheets?|statements?\s+of\s+financial\s+position", re.I)),
    ("equity", re.compile(r"(?:stockholders|shareholders)['’]?\s+equity\s+statements?|statements?\s+of\s+(?:stockholders|shareholders)['’]?\s+equity", re.I)),
    ("income", re.compile(r"income\s+statements?|statements?\s+of\s+(?:consolidated\s+)?(?:income|operations|earnings)", re.I)),
)

_MONTH = r"(?:January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec)\.?"
_MONTH_NUM = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
HEADER_CELL_RE = re.compile(
    rf"^(?:(?:fiscal\s+)?years?\s+ended\s+)?(?:({_MONTH})\s+(\d{{1,2}}),?\s*)?((?:19|20)\d{{2}})$", re.I)
ENDED_RE = re.compile(rf"ended\s+({_MONTH})\s+(\d{{1,2}})", re.I)
ANNUAL_RE = re.compile(r"(?:fiscal\s+)?years?\s+ended|twelve\s+months\s+ended|12\s+months\s+ended", re.I)
NONANNUAL_RE = re.compile(r"(?:three|six|nine|3|6|9)\s+months\s+ended|quarters?\s+ended|quarterly", re.I)
SCALE_RE = re.compile(r"(?:\(|dollars\s+|amounts\s+|\bUSD\s+|US\$\s*)\s*in\s+(thousands|millions|billions)\b", re.I)
SCALE_MULTIPLIER = {"thousands": 1_000, "millions": 1_000_000, "billions": 1_000_000_000}
SEGMENT_RE = re.compile(r"\bsegments?\b", re.I)
# Statements for something other than the consolidated group: parent-only / standalone / consolidating schedules.
NON_CONSOLIDATED_RE = re.compile(
    r"\bparent(?:[- ]company)?(?:[- ]only)?\b|\bstand-?alone\b|\bunconsolidated\b|\bnot\s+consolidated\b|"
    r"\bconsolidating\b|\bregistrant[- ]only\b|\b(?:statements?|information|results|schedule)\s+of\s+(?:the\s+)?registrant\b|"
    r"\bcompany[- ]only\b|\bissuer[- ]only\b|\bguarantor", re.I)
NONGAAP_RE = re.compile(r"non-?\s?gaap|\badjusted\b|pro\s*forma", re.I)
CONSOLIDATION_RE = re.compile(
    r"consolidated\s+financial\s+statements[^.]{0,200}?(?:include|includes|present|presents|comprise)[^.]{0,250}?subsidiar",
    re.I,
)
GAAP_RE = re.compile(
    r"in\s+accordance\s+with\s+(?:U\.S\.\s+|US\s+)?(?:accounting\s+principles\s+generally\s+accepted|generally\s+accepted\s+accounting\s+principles|GAAP)",
    re.I,
)
_USD_TOKEN = r"(?:(?:U\.S\.|US|United\s+States)\s+dollars?|US\$|\bUSD\b)"
_SCALE_WORDS = r"(?:thousands|millions|billions)"
# Words that mark a sentence/line as being about something OTHER than the statements' reporting currency: debt and its
# denomination, hedging, exchange-rate movements, functional currency of subsidiaries, pricing/payment commentary.
# A sentence containing any of them is never accepted as reporting-currency evidence (fail safe).
COMMENTARY_RE = re.compile(
    r"\b(?:notes?|debt|bonds?|borrowings?|loans?|debentures?|credit\s+facilit\w+|hedg\w*|swaps?|forwards?|exchange\s+rates?|"
    r"foreign\s+exchange|strengthen\w*|weaken\w*|appreciat\w*|depreciat\w*|constant|functional|denominated|quoted|priced|pay)\b", re.I)
# (a) explicit USD statement in a statement header / heading / scale line: a parenthetical unit line naming USD, or
# "expressed/stated/presented/reported in ... U.S. dollars".
HEADER_USD_PAREN_RE = re.compile(rf"\([^()]*{_USD_TOKEN}[^()]*\)", re.I)
HEADER_USD_VERB_RE = re.compile(rf"\b(?:expressed|stated|presented|reported)\s+in\s+(?:{_SCALE_WORDS}\s+of\s+)?{_USD_TOKEN}", re.I)
# (b) a document sentence that clearly governs the financial statements or establishes the reporting/presentation currency.
_FS = r"(?:consolidated\s+)?financial\s+statements"
GOVERNING_USD_RES = tuple(re.compile(p, re.I) for p in (
    rf"{_FS}[^.;]{{0,80}}?\b(?:presented|expressed|stated|reported)\s+in\s+(?:{_SCALE_WORDS}\s+of\s+)?{_USD_TOKEN}",
    rf"\b(?:present|report|express|state)\s+(?:our|its|the)\s+{_FS}\s+in\s+{_USD_TOKEN}",
    rf"\b(?:reporting|presentation)\s+currency[^.;]{{0,40}}?\b(?:is|was|of|being)\s+(?:the\s+)?{_USD_TOKEN}",
    rf"{_USD_TOKEN}\s+(?:is|as)\s+(?:our|the|its|the\s+company'?s)\s+(?:reporting|presentation)\s+currency",
))
ISO4217_NS = "http://www.xbrl.org/2003/iso4217"
_OTHER_CURRENCY = (
    r"Canadian\s+dollars?|\bCAD\b|C\$|Australian\s+dollars?|\bAUD\b|euros?\b|\bEUR\b|\u20ac|"
    r"pounds?\s+sterling|British\s+pounds?|\bGBP\b|\u00a3|japanese\s+yen|\byen\b|\bJPY\b|\u00a5|renminbi|\bRMB\b|"
    r"\bCNY\b|Swiss\s+francs?|\bCHF\b|Indian\s+rupees?|\bINR\b|Mexican\s+pesos?|New\s+Zealand\s+dollars?|"
    r"Singapore\s+dollars?|Hong\s+Kong\s+dollars?|Brazilian\s+reais|Korean\s+won|Swedish\s+kron(?:a|or)")
OTHER_CURRENCY_RE = re.compile(_OTHER_CURRENCY, re.I)
# Document-level contradiction: only phrases that state the *reporting* currency. Functional currencies of subsidiaries and
# the denomination of debt ("denominated in euros") say nothing about the statements' currency and are deliberately not matched.
DOC_OTHER_CURRENCY_RE = re.compile(
    r"(?:(?:reporting|presentation)\s+currency|(?:presented|reported|expressed|stated)\s+in)"
    rf"\s+(?:(?:is|are|of)\s+)?(?:the\s+)?(?:(?:millions|thousands|billions)\s+of\s+)?(?:{_OTHER_CURRENCY})", re.I)
IX_SCALE_RE = re.compile(r"[+-]?[0-9]{1,3}")
IX_SCALE_RANGE = (0, 12)  # monetary amounts; supported statement scales are 3, 6, 9


def prior_year_end(d: date) -> date:
    try:
        return d.replace(year=d.year - 1)
    except ValueError:  # Feb 29
        return date(d.year - 1, 2, 28)


def period_label(end: date) -> str:
    return f"FY{end.year}"


def norm_label(text: str) -> str:
    t = text.replace("’", "'").lower().strip().rstrip(":").strip()
    t = re.sub(r"\s*(?:\(\s*[a-z0-9]{1,2}\s*\)|\*+)$", "", t)
    return re.sub(r"\s+", " ", t)


def match_metric(row: Row) -> MetricSpec | None:
    if len(row.logical) < 2:
        return None
    first = row.logical[0].text
    if looks_like_amount(first) or is_dash(first):
        return None
    spec = _LABEL_INDEX.get(norm_label(first))
    if spec is None:
        return None
    if not any(looks_like_amount(c.text) or is_dash(c.text) for c in row.logical[1:]):
        return None
    return spec


# ---------------------------------------------------------------- table context


@dataclass
class Column:
    header_cell_index: int
    header_text: str
    end: date | None


@dataclass
class TableContext:
    kind: str | None = None
    title: str | None = None
    title_where: str = ""
    segment: bool = False
    non_consolidated: bool = False
    non_gaap: bool = False
    header_row: int | None = None
    columns: list[Column] = field(default_factory=list)
    period_sources: list[tuple[str, str]] = field(default_factory=list)
    scale_multiplier: int | None = None
    scale_evidence: tuple[str, str] | None = None
    currency_evidence: "CurrencyEvidence | None" = None  # (a) header/scale-line evidence only
    problems: list[tuple[str, str]] = field(default_factory=list)


def _heading_like(text: str) -> bool:
    return len(text) <= 200


def _parse_date(month: str, day: str, year: int) -> date:
    return date(year, _MONTH_NUM[month.lower()[:3]], int(day))


@dataclass(frozen=True)
class CurrencyEvidence:
    kind: str  # statement_header_usd | document_statement_usd | ixbrl_unit_usd
    text: str
    where: str
    unit_id: str | None = None
    unit_measure: str | None = None
    unit_locator: str | None = None


def local_usd_statement(text: str) -> bool:
    """(a) True if a header/heading/scale-line text explicitly states USD and is not commentary."""
    if COMMENTARY_RE.search(text):
        return False
    return bool(HEADER_USD_PAREN_RE.search(text) or HEADER_USD_VERB_RE.search(text))


def _sentences(block: str) -> list[str]:
    protected = block.replace("U.S.", "U\u2024S\u2024")
    return [part.replace("U\u2024S\u2024", "U.S.").strip() for part in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9(\u201c\"])", protected) if part.strip()]


def find_governing_usd_statement(doc: Document) -> CurrencyEvidence | None:
    """(b) the first document sentence that clearly governs the financial statements' (or the company's) reporting currency."""
    for i, block in enumerate(doc.blocks):
        for sentence in _sentences(block):
            if COMMENTARY_RE.search(sentence):
                continue
            if any(rx.search(sentence) for rx in GOVERNING_USD_RES):
                return CurrencyEvidence("document_statement_usd", sentence, f"text block {i}")
    return None


@dataclass(frozen=True)
class UnitResolution:
    status: str  # "usd" | "other_currency" | "unresolved"
    reason: str
    evidence: CurrencyEvidence | None = None
    measure: str | None = None


def resolve_cell_unit(cell: LogicalCell, doc: Document, table: Table, row: Row) -> UnitResolution:
    """(c) Resolve THIS amount's own inline-XBRL unitRef within this document to a simple monetary unit.

    An unrelated USD unit elsewhere in the document is irrelevant: only the unit the amount itself references counts."""
    if cell.ix is None:
        return UnitResolution("unresolved", "the amount is not inline-XBRL tagged")
    ref = cell.ix.get("unitref")
    if not ref:
        return UnitResolution("unresolved", "the amount's ix:nonFraction has no unitRef")
    defs = [u for u in doc.units if u.unit_id == ref]
    if not defs:
        return UnitResolution("unresolved", f"unitRef {ref!r} is not defined under ix:resources")
    if len(defs) > 1:
        return UnitResolution("unresolved", f"unit id {ref!r} is defined {len(defs)} times (ambiguous)")
    unit = defs[0]
    if unit.has_divide or len(unit.measures) != 1:
        return UnitResolution("unresolved", f"unit {ref!r} is not a simple single-measure unit")
    measure = unit.measures[0]
    prefix, _, local = measure.partition(":")
    namespaces = doc.namespaces.get(prefix, set())
    if not local or len(namespaces) != 1:
        return UnitResolution("unresolved", f"measure {measure!r}: namespace prefix {prefix!r} is undeclared or ambiguous", measure=measure)
    if next(iter(namespaces)) != ISO4217_NS:
        return UnitResolution("unresolved", f"measure {measure!r} is not in the ISO 4217 namespace", measure=measure)
    if not re.fullmatch(r"[A-Z]{3}", local):
        return UnitResolution("unresolved", f"measure {measure!r} is not an upper-case ISO 4217 code", measure=measure)
    if local != "USD":
        return UnitResolution("other_currency", f"unit {ref!r} measures {measure}", measure=measure)
    locator = f"ix:resources/xbrli:unit[{unit.index}] id={unit.unit_id!r}"
    evidence = CurrencyEvidence(
        "ixbrl_unit_usd",
        f'<xbrli:unit id="{unit.unit_id}"><xbrli:measure>{measure}</xbrli:measure></xbrli:unit>',
        f"{locator}; referenced by ix:nonFraction unitRef={ref!r} at table {table.index} row {row.index} cell {cell.cell_index}",
        unit_id=unit.unit_id, unit_measure=measure, unit_locator=locator)
    return UnitResolution("usd", "resolved", evidence, measure)


PERIOD_LABEL_RE = re.compile("|".join(r.pattern for r in (ANNUAL_RE, NONANNUAL_RE, ENDED_RE)), re.I)


def _header_year_cells(row: Row) -> list[LogicalCell] | None:
    """Year/date cells of a header row, or None if this is not a header row.

    A header row may carry a period label ("Year Ended June 30,") or a scale statement next to its year/date
    cells, but any other text (e.g. a row label) disqualifies it. At least two year/date cells are required.
    """
    years: list[LogicalCell] = []
    for cell in row.logical:
        if HEADER_CELL_RE.match(cell.text):
            years.append(cell)
        elif PERIOD_LABEL_RE.search(cell.text) or SCALE_RE.search(cell.text):
            continue
        else:
            return None
    return years if len(years) >= 2 else None


def analyze_table(table: Table) -> TableContext:
    ctx = TableContext()
    headings = [t for t in table.pre_context if _heading_like(t)]

    header_cells: list[LogicalCell] = []
    for r_idx, row in enumerate(table.rows):
        found = _header_year_cells(row)
        if found is not None:
            ctx.header_row, header_cells = r_idx, found
            break
    header_rows = table.rows[: ctx.header_row + 1] if ctx.header_row is not None else table.rows[:3]
    header_text = " / ".join(r.text() for r in header_rows if r.text())

    # statement kind: nearest heading above the table first, then the table's own header rows
    candidates: list[tuple[str, str]] = [(t, f"text block before table {table.index}") for t in reversed(headings)]
    candidates += [(r.text(), f"table {table.index} row {r.index}") for r in header_rows if r.text()]
    for text, where in candidates:
        if len(text) > 120:
            continue
        for kind, pattern in STATEMENT_PATTERNS:
            if pattern.search(text):
                ctx.kind, ctx.title, ctx.title_where = kind, text, where
                break
        if ctx.kind:
            break

    local = [(t, f"text block before table {table.index}") for t in headings[-4:]] + [
        (r.text(), f"table {table.index} row {r.index}") for r in header_rows if r.text()]
    ctx.segment = any(SEGMENT_RE.search(t) for t, _ in local)
    ctx.non_consolidated = any(NON_CONSOLIDATED_RE.search(t) for t, _ in local)
    ctx.non_gaap = any(NONGAAP_RE.search(t) for t, _ in local)

    # period
    period_text = " ".join([header_text] + headings)
    annual, nonannual = bool(ANNUAL_RE.search(period_text)), bool(NONANNUAL_RE.search(period_text))
    ctx.period_sources.append((header_text, f"table {table.index} rows 0-{ctx.header_row}" if ctx.header_row is not None else f"table {table.index} top rows"))
    for h in headings:
        if ANNUAL_RE.search(h) or ENDED_RE.search(h):
            ctx.period_sources.append((h, f"text block before table {table.index}"))
    if ctx.header_row is None:
        ctx.problems.append(("missing_period_evidence", f"table {table.index}: no year header row found"))
    elif nonannual:
        ctx.problems.append(("period_not_annual", f"table {table.index}: header describes a non-annual period: {header_text!r}"))
    elif not annual:
        ctx.problems.append(("missing_period_evidence", f"table {table.index}: no 'Year Ended' wording to establish an annual duration"))
    if ctx.header_row is not None:
        # a later header row with different years means the table holds more than one period block
        for later in table.rows[ctx.header_row + 1:]:
            other = _header_year_cells(later)
            if other is not None and [c.text for c in other] != [c.text for c in header_cells]:
                ctx.problems.append(("ambiguous_period_header",
                                     f"table {table.index}: row {later.index} is a second, different period header"))
                break
        ended = ENDED_RE.search(period_text)
        for lc in header_cells:
            m = HEADER_CELL_RE.match(lc.text)
            end = None
            try:
                if m and m.group(1):
                    end = _parse_date(m.group(1), m.group(2), int(m.group(3)))
                elif m and ended:
                    end = _parse_date(ended.group(1), ended.group(2), int(m.group(3)))
            except (ValueError, KeyError):
                end = None
            ctx.columns.append(Column(header_cell_index=lc.cell_index, header_text=lc.text, end=end))
        if any(c.end is None for c in ctx.columns) and not nonannual:
            ctx.problems.append(("missing_period_evidence", f"table {table.index}: year/month/day of period end not stated"))
        keys = [c.end if c.end is not None else c.header_text for c in ctx.columns]
        if len(set(keys)) != len(keys):
            ctx.problems.append(("duplicate_period_columns", f"table {table.index}: the same period appears in more than one column"))

    # scale
    found: dict[int, tuple[str, str]] = {}
    for text, where in [(h, f"text block before table {table.index}") for h in headings] + [
        (r.text(), f"table {table.index} row {r.index}") for r in header_rows
    ]:
        for m in SCALE_RE.finditer(text):
            found.setdefault(SCALE_MULTIPLIER[m.group(1).lower()], (text, where))
    if len(found) > 1:
        ctx.problems.append(("ambiguous_scale", f"table {table.index}: conflicting scale statements {sorted(found)}"))
    elif not found:
        ctx.problems.append(("missing_scale_evidence", f"table {table.index}: no '(In millions)'-style scale statement"))
    else:
        (ctx.scale_multiplier, ctx.scale_evidence), = found.items()

    # currency (USD-only MVP). Contradictions block. Affirmative evidence from the statement's own header/scale line is (a);
    # (b) document sentences and (c) the amount's own inline-XBRL unit are resolved later, per fact. '$' alone is never evidence.
    contradiction = next((m.group(0) for t, _ in local if (m := OTHER_CURRENCY_RE.search(t))), None)
    if contradiction is not None:
        ctx.problems.append(("currency_contradiction",
                             f"table {table.index}: statement text names a non-USD currency ({contradiction!r}); only USD is supported"))
    else:
        found_a = next(((t, w) for t, w in local if local_usd_statement(t)), None)
        if found_a is not None:
            ctx.currency_evidence = CurrencyEvidence("statement_header_usd", found_a[0], found_a[1])
    return ctx


# ---------------------------------------------------------------- document-level evidence


def _excerpt_around(text: str, start: int, end: int, pad: int = 150) -> str:
    lo, hi = max(0, start - pad), min(len(text), end + pad)
    snippet = text[lo:hi].strip()
    return ("…" if lo > 0 else "") + snippet + ("…" if hi < len(text) else "")


def find_evidence(doc: Document, pattern: re.Pattern[str]) -> tuple[str, str] | None:
    for i, block in enumerate(doc.blocks):
        if m := pattern.search(block):
            return _excerpt_around(block, m.start(), m.end()), f"text block {i}"
    return None


# ---------------------------------------------------------------- candidates


@dataclass
class Candidate:
    occurrence: Occurrence
    spec: MetricSpec
    table: Table
    row: Row
    ctx: TableContext
    end: date
    currency: CurrencyEvidence


def _scale_exponent(multiplier: int) -> int:
    return int(round(math.log10(multiplier)))


def _locator(table: Table, row: Row, cell: LogicalCell, col: Column, label: str, ctx: TableContext, checksum: str) -> Locator:
    return Locator(
        section=table.section,
        statement=ctx.title or "",
        printed_page=table.printed_page,
        row=label,
        column=col.header_text,
        table_index=table.index,
        row_index=row.index,
        cell_index=cell.cell_index,
        header_row_index=ctx.header_row if ctx.header_row is not None else -1,
        header_cell_index=col.header_cell_index,
        element_id=cell.element_id or row.element_id or table.element_id,
        source_checksum=checksum,
    )


def _ix_scale(ix: dict[str, str]) -> int | str:
    """Inline-XBRL scale exponent, or an error string. Absent attribute means 0; present-but-malformed is invalid."""
    raw = ix.get("scale")
    if raw is None:
        return 0
    if not IX_SCALE_RE.fullmatch(raw.strip()):
        return f"scale={raw!r} is not an integer"
    value = int(raw.strip())
    if not IX_SCALE_RANGE[0] <= value <= IX_SCALE_RANGE[1]:
        return f"scale={value} is outside the supported range {IX_SCALE_RANGE[0]}..{IX_SCALE_RANGE[1]}"
    return value


def extract_facts(html: bytes | str, target: Target, checksum: str) -> FactTable:
    doc = parse_document(html)
    consolidation = find_evidence(doc, CONSOLIDATION_RE)
    gaap = find_evidence(doc, GAAP_RE)
    governing_usd = find_governing_usd_statement(doc)
    currency_conflict = find_evidence(doc, DOC_OTHER_CURRENCY_RE)
    wanted_ends = {target.fiscal_period_end, prior_year_end(target.fiscal_period_end)}

    issues: list[Issue] = []
    exclusions: list[Exclusion] = []
    candidates: list[Candidate] = []

    for table in doc.tables:
        matches = [(row, spec) for row in table.rows if (spec := match_metric(row))]
        if not matches:
            continue
        ctx = analyze_table(table)
        for row, spec in matches:
            label_cell = row.logical[0]

            def exclude(reason: str) -> None:
                exclusions.append(Exclusion(
                    reason=reason, metric=spec.key, row_label=label_cell.text, table_index=table.index,
                    row_index=row.index, raw_amount_text=row.text()))

            def issue(code: str, message: str, period: str | None = None) -> None:
                # A problem inside a corroborating-only statement cannot by itself invalidate the authoritative fact.
                issues.append(Issue(code=code, message=message, metric=spec.key, period_label=period, table_index=table.index,
                                    row_index=row.index, statement_kind=ctx.kind, blocking=ctx.kind not in spec.corroborating))

            # Local scope/basis cues first: they override any document-level default and give the most informative reason.
            if ctx.segment:
                exclude("scope_segment_differs_from_consolidated")
                continue
            if ctx.non_consolidated:
                exclude("scope_non_consolidated_entity_differs_from_consolidated")
                continue
            if ctx.non_gaap:
                exclude("basis_non_gaap_differs_from_gaap")
                continue
            if ctx.kind is None:
                exclude("statement_not_identified")
                continue
            if ctx.kind not in (spec.authoritative, *spec.corroborating):
                exclude(f"statement_kind_not_applicable:{ctx.kind}")
                continue
            if ctx.problems:
                for code, message in ctx.problems:
                    issue(code, message)
                continue
            amounts = [c for c in row.logical[1:] if looks_like_amount(c.text) or is_dash(c.text)]
            if len(amounts) != len(ctx.columns):
                issue("column_alignment", f"{len(amounts)} amount cells vs {len(ctx.columns)} year columns in row {label_cell.text!r}")
                continue
            assert ctx.scale_multiplier is not None
            for col, cell in zip(ctx.columns, amounts):
                if col.end not in wanted_ends:
                    continue
                plabel = period_label(col.end)
                if cell.ambiguous_visibility:
                    issue("ambiguous_text_visibility",
                          f"{label_cell.text!r} {plabel}: amount cell contains white/transparent-coloured text; visibility cannot be established", plabel)
                    continue
                if is_dash(cell.text):
                    issue("missing_amount", f"{label_cell.text!r} has no amount for {plabel} (cell {cell.text!r})", plabel)
                    continue
                try:
                    value = parse_amount(cell.text)
                except AmountError as err:
                    issue("unparseable_amount", str(err), plabel)
                    continue
                if cell.ix is not None:
                    ix_exp = _ix_scale(cell.ix)
                    if isinstance(ix_exp, str):
                        issue("ix_scale_invalid", f"inline XBRL {ix_exp}", plabel)
                        continue
                    if cell.ix.get("sign") not in (None, "-"):
                        issue("ix_sign_invalid", f"inline XBRL sign={cell.ix.get('sign')!r} is not '-' or absent", plabel)
                        continue
                    if ix_exp != _scale_exponent(ctx.scale_multiplier):
                        issue("ix_scale_mismatch",
                              f"inline XBRL scale={ix_exp} vs statement scale 10^{_scale_exponent(ctx.scale_multiplier)}", plabel)
                        continue
                    if (value < 0) != (cell.ix.get("sign") == "-"):
                        issue("ix_sign_mismatch", f"inline XBRL sign={cell.ix.get('sign')!r} vs displayed {cell.text!r}", plabel)
                        continue
                unit = resolve_cell_unit(cell, doc, table, row)
                if unit.status == "other_currency":
                    issue("currency_contradiction", f"{label_cell.text!r} {plabel}: {unit.reason}; only USD is supported", plabel)
                    continue
                currency = ctx.currency_evidence or governing_usd or unit.evidence
                if currency is None:
                    issue("missing_currency_evidence",
                          f"{label_cell.text!r} {plabel}: no explicit USD reporting-currency statement in the statement header or "
                          f"scale line, none in the document, and the amount's own inline-XBRL unit does not establish it "
                          f"({unit.reason}). A dollar sign alone is not evidence.", plabel)
                    continue
                occ = Occurrence(
                    metric=spec.key, original_label=label_cell.text, raw_amount_text=cell.text, reported_value=value,
                    scale_multiplier=ctx.scale_multiplier, normalized_value=value * ctx.scale_multiplier,
                    period_label=plabel, statement_kind=ctx.kind, statement_title=ctx.title or "",
                    locator=_locator(table, row, cell, col, label_cell.text, ctx, checksum),
                )
                candidates.append(Candidate(occ, spec, table, row, ctx, col.end, currency))

    facts, conflicts = _decide(candidates, target, checksum, consolidation, gaap, currency_conflict, issues)

    # every wanted metric/period without a fact must be explained by a blocking issue or a conflict
    have = {(f.metric, f.period.end) for f in facts}
    for spec in METRICS:
        for end in sorted(wanted_ends, reverse=True):
            if (spec.key, end) in have:
                continue
            plabel = period_label(end)
            explained = any(i.blocking and i.metric == spec.key and i.period_label in (plabel, None) for i in issues) or any(
                c.metric == spec.key and c.period_label == plabel for c in conflicts)
            if not explained:
                issues.append(Issue(code="not_found", message=f"no usable authoritative occurrence for {spec.key} {plabel}",
                                    metric=spec.key, period_label=plabel))

    return FactTable(
        source=SourceRef(
            company=target.company, cik=target.cik, form=target.form, accession=target.accession,
            fiscal_period_end=target.fiscal_period_end, filing_date=target.filing_date, url=target.url, checksum=checksum),
        facts=facts, conflicts=conflicts, issues=issues, exclusions=exclusions,
    )


def _decide(
    candidates: list[Candidate],
    target: Target,
    checksum: str,
    consolidation: tuple[str, str] | None,
    gaap: tuple[str, str] | None,
    currency_conflict: tuple[str, str] | None,
    issues: list[Issue],
) -> tuple[list[Fact], list[Conflict]]:
    groups: dict[tuple[str, date], list[Candidate]] = {}
    for c in candidates:
        groups.setdefault((c.spec.key, c.end), []).append(c)

    facts: list[Fact] = []
    conflicts: list[Conflict] = []
    for spec in METRICS:
        for end in sorted({e for (k, e) in groups if k == spec.key}, reverse=True):
            group = groups[(spec.key, end)]
            plabel = period_label(end)
            if consolidation is None:
                issues.append(Issue(code="missing_scope_evidence", metric=spec.key, period_label=plabel,
                                    message="no text establishing that the statements are consolidated"))
                continue
            if gaap is None:
                issues.append(Issue(code="missing_basis_evidence", metric=spec.key, period_label=plabel,
                                    message="no text establishing a GAAP basis of preparation"))
                continue
            if currency_conflict is not None:
                issues.append(Issue(code="currency_contradiction", metric=spec.key, period_label=plabel,
                                    message=f"document states a non-USD reporting currency ({currency_conflict[1]}: {currency_conflict[0]!r}); only USD is supported"))
                continue
            primary = [c for c in group if c.ctx.kind == spec.authoritative]
            if not primary:
                issues.append(Issue(code="no_authoritative_occurrence", metric=spec.key, period_label=plabel,
                                    message=f"only non-authoritative statements contain {spec.key} {plabel}"))
                continue
            basis = "US GAAP" if re.search(r"U\.S\.|US\b|United\s+States", gaap[0]) else "GAAP (jurisdiction not stated)"
            if len({c.occurrence.normalized_value for c in primary}) > 1:
                conflicts.append(Conflict(
                    kind="incompatible_primary_duplicates", metric=spec.key, period_label=plabel, scope="consolidated",
                    accounting_basis=basis, resolved=False,
                    decision="No fact emitted: authoritative statements disagree on the normalized value.",
                    occurrences=[c.occurrence for c in group]))
                continue
            chosen = primary[0]
            same = [c for c in group if c is not chosen and c.occurrence.normalized_value == chosen.occurrence.normalized_value]
            differ = [c for c in group if c.occurrence.normalized_value != chosen.occurrence.normalized_value]
            if differ:
                conflicts.append(Conflict(
                    kind="corroboration_mismatch", metric=spec.key, period_label=plabel, scope="consolidated",
                    accounting_basis=basis, resolved=False,
                    decision="Authoritative-statement value retained; disagreeing non-authoritative occurrences need review.",
                    occurrences=[chosen.occurrence] + [c.occurrence for c in differ]))
            facts.append(_build_fact(chosen, same, bool(differ), target, checksum, consolidation, gaap, basis, issues))
    return [f for f in facts if f is not None], conflicts


def _build_fact(
    chosen: Candidate,
    corroborating: list[Candidate],
    has_disagreement: bool,
    target: Target,
    checksum: str,
    consolidation: tuple[str, str],
    gaap: tuple[str, str],
    basis: str,
    issues: list[Issue],
) -> Fact | None:
    occ, ctx, table, row = chosen.occurrence, chosen.ctx, chosen.table, chosen.row
    excerpts = [Excerpt(kind="amount", text=row.text(), where=f"table {table.index} row {row.index}")]
    for text, where in ctx.period_sources:
        excerpts.append(Excerpt(kind="period", text=text, where=where))
    assert ctx.scale_evidence is not None
    excerpts.append(Excerpt(kind="scale", text=ctx.scale_evidence[0], where=ctx.scale_evidence[1]))
    excerpts.append(Excerpt(kind="scope", text=ctx.title or "", where=ctx.title_where))
    excerpts.append(Excerpt(kind="scope", text=consolidation[0], where=consolidation[1]))
    excerpts.append(Excerpt(kind="basis", text=gaap[0], where=gaap[1]))
    cur = chosen.currency
    excerpts.append(Excerpt(kind="currency", text=cur.text, where=cur.where))
    end = chosen.end
    try:
        return Fact(
            evidence_id=f"{chosen.spec.code}_{end.year}", metric=occ.metric, original_label=occ.original_label,
            raw_amount_text=occ.raw_amount_text, reported_value=occ.reported_value, currency="USD",
            currency_evidence_kind=cur.kind, currency_unit_id=cur.unit_id, currency_unit_measure=cur.unit_measure,
            currency_unit_locator=cur.unit_locator,
            scale_multiplier=occ.scale_multiplier, normalized_value=occ.normalized_value,
            period=Period(label=occ.period_label, start=prior_year_end(end) + timedelta(days=1), end=end),
            scope="consolidated", scope_description=f"{target.company} and subsidiaries, consolidated",
            accounting_basis=basis, accession=target.accession, source_url=target.url, source_checksum=checksum,
            locator=occ.locator, excerpts=excerpts, corroborating_occurrences=[c.occurrence for c in corroborating],
            conflict_status="authoritative_retained_with_disagreeing_occurrences" if has_disagreement else "none",
        )
    except ValueError as err:
        issues.append(Issue(code="fact_validation_failed", message=str(err), metric=occ.metric, period_label=occ.period_label,
                            table_index=table.index, row_index=row.index))
        return None
