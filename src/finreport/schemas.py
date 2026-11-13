"""Pydantic schemas for source-linked facts and their validation rules.

Decimal values serialize to JSON strings so amounts stay exact.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ACCESSION_RE = re.compile(r"^\d{10}-\d{2}-\d{6}$")
EXCERPT_KINDS = ("amount", "period", "scale", "scope", "basis", "currency")
VALID_MULTIPLIERS = (1_000, 1_000_000, 1_000_000_000)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Period(Strict):
    label: str
    start: date
    end: date
    type: Literal["annual_duration"] = "annual_duration"

    @model_validator(mode="after")
    def _complete_annual_duration(self) -> "Period":
        days = (self.end - self.start).days + 1
        # 364/371 allow 52/53-week fiscal years; 365/366 calendar-length years.
        if not 364 <= days <= 371:
            raise ValueError(f"period {self.label} spans {days} days; not a complete annual duration")
        return self


class Locator(Strict):
    """Reproducible pointer into the checksum-pinned HTML.

    table_index: 0-based index of top-level <table> elements in document order.
    row_index:   0-based index of <tr> elements inside that table.
    cell_index:  0-based index of direct <td>/<th> children of that row.
    Resolve with finreport.parsing.resolve_locator against the cached file.
    """

    section: str | None = None
    statement: str
    printed_page: int | None = None
    row: str
    column: str
    table_index: int
    row_index: int
    cell_index: int
    header_row_index: int
    header_cell_index: int
    element_id: str | None = None
    source_checksum: str
    locator_version: int = 1

    @model_validator(mode="after")
    def _checksum(self) -> "Locator":
        if not SHA256_RE.match(self.source_checksum):
            raise ValueError("locator source_checksum must be 64 lowercase hex chars")
        return self


class Excerpt(Strict):
    kind: Literal["amount", "period", "scale", "scope", "basis", "currency"]
    text: str = Field(min_length=1)
    where: str


class Occurrence(Strict):
    """One place in the filing where a metric/period amount appears."""

    metric: str
    original_label: str
    raw_amount_text: str
    reported_value: Decimal
    scale_multiplier: int
    normalized_value: Decimal
    period_label: str
    statement_kind: str
    statement_title: str
    locator: Locator


class Fact(Strict):
    evidence_id: str
    metric: str
    original_label: str
    raw_amount_text: str
    reported_value: Decimal
    # What governs the statements' reporting currency (USD-only MVP). A dollar sign alone, debt denomination, a subsidiary's
    # functional currency and market commentary are NOT evidence. Kinds:
    #   statement_header_usd   explicit USD statement in the financial statements' header/heading or scale line
    #   document_statement_usd explicit document sentence tying the financial statements / reporting currency to USD
    #   ixbrl_unit_usd         fallback: the amount's own inline-XBRL unitRef resolves to a simple iso4217:USD unit
    currency: str = Field(min_length=3, max_length=3)
    currency_evidence_kind: Literal["statement_header_usd", "document_statement_usd", "ixbrl_unit_usd"]
    currency_unit_id: str | None = None
    currency_unit_measure: str | None = None
    currency_unit_locator: str | None = None
    scale_multiplier: int
    normalized_value: Decimal
    period: Period
    scope: Literal["consolidated", "segment"]
    scope_description: str
    accounting_basis: str
    accession: str
    source_url: str
    source_checksum: str
    locator: Locator
    excerpts: list[Excerpt]
    corroborating_occurrences: list[Occurrence] = Field(default_factory=list)
    conflict_status: Literal["none", "authoritative_retained_with_disagreeing_occurrences"] = "none"

    @model_validator(mode="after")
    def _validate(self) -> "Fact":
        unit_fields = (self.currency_unit_id, self.currency_unit_measure, self.currency_unit_locator)
        if self.currency_evidence_kind == "ixbrl_unit_usd":
            if not all(unit_fields) or self.currency_unit_measure != "iso4217:USD":
                raise ValueError("ixbrl_unit_usd evidence must preserve the unit id, the iso4217:USD measure and the unit locator")
        elif any(unit_fields):
            raise ValueError("unit details are only valid for ixbrl_unit_usd evidence")
        if self.scale_multiplier not in VALID_MULTIPLIERS:
            raise ValueError(f"unsupported scale multiplier {self.scale_multiplier}")
        if self.normalized_value != self.reported_value * self.scale_multiplier:
            raise ValueError("normalized_value != reported_value * scale_multiplier")
        if not ACCESSION_RE.match(self.accession):
            raise ValueError(f"malformed accession {self.accession!r}")
        if not SHA256_RE.match(self.source_checksum):
            raise ValueError("source_checksum must be 64 lowercase hex chars")
        if self.locator.source_checksum != self.source_checksum:
            raise ValueError("locator checksum differs from fact checksum")
        present = {e.kind for e in self.excerpts}
        missing = [k for k in EXCERPT_KINDS if k not in present]
        if missing:
            raise ValueError(f"missing supporting excerpts: {missing}")
        return self


class Conflict(Strict):
    kind: Literal["incompatible_primary_duplicates", "corroboration_mismatch"]
    metric: str
    period_label: str
    scope: str
    accounting_basis: str
    resolved: bool
    decision: str
    occurrences: list[Occurrence]


class Issue(Strict):
    """A problem that stopped (or could stop) a fact from being produced.

    `blocking` is False only for problems found in a statement that is merely corroborating for the
    metric (e.g. net income in the cash-flow statement); such issues cannot by themselves invalidate a
    fact whose authoritative statement is clean. Everything else blocks acceptance.
    """

    code: str
    message: str
    metric: str | None = None
    period_label: str | None = None
    table_index: int | None = None
    row_index: int | None = None
    statement_kind: str | None = None
    blocking: bool = True


class Exclusion(Strict):
    """An occurrence deliberately not used, with the reason (e.g. segment scope)."""

    reason: str
    metric: str
    row_label: str
    table_index: int
    row_index: int
    period_label: str | None = None
    raw_amount_text: str | None = None


class SourceRef(Strict):
    company: str
    cik: str
    form: str
    accession: str
    fiscal_period_end: date
    filing_date: date
    url: str
    checksum: str


class FactTable(Strict):
    schema_version: int = 1
    source: SourceRef
    facts: list[Fact]
    conflicts: list[Conflict]
    issues: list[Issue]
    exclusions: list[Exclusion]
