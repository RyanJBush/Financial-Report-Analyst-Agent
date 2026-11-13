"""Deterministic question answering and calculation over a VERIFIED fact table. No model, no narrative text.

What this module may read: the fact table produced by extraction, plus (through `load_factbook`) the manifest and the
cached source so that the evidence can be re-verified. It must NOT read the evaluation expectations and must not import the
evaluator module; tests enforce both.

Three answer kinds, all returned as a structured `Answer`:
  lookup       "What was Microsoft's consolidated revenue in FY2024?"                       -> one cited fact
  change       "How much did consolidated revenue change from FY2023 to FY2024?"            -> two cited facts + a calculation record
  unsupported  anything else, including forecasts ("What will ... be in FY2027?")           -> status `unresolved`, a reason code,
               and NO evidence, NO number and NO citation (nothing is invented)

Statuses: `answered`; `unresolved` (the question cannot be answered from this evidence; why is in `reason_code`); `refused`
(the evidence needed exists but is not verified: source check failed, a flagged conflict, a blocking issue, inconsistent fact).

Calculation rules: arithmetic is `Decimal` on NORMALIZED amounts (reported value x scale multiplier), so facts reported in
different scales compare correctly. Percentage change is `(current - prior) / prior * 100`, computed at 28 significant digits
(round-half-even in the division, as Python's default Decimal context) and DISPLAYED rounded to two places with ROUND_HALF_UP.
It is computed only when the prior amount is positive; with a zero or negative prior amount the absolute change is still
returned together with an explicit reason for withholding the percentage. Two facts are comparable only if metric, scope,
accounting basis, currency and source filing match and both are distinct annual periods of comparable length.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Context, Decimal, localcontext
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError, model_validator

from finreport.schemas import Excerpt, Fact, FactTable, Locator, Strict

ANSWER_SCHEMA_VERSION = 1
PERCENT_PRECISION = 28
PERCENT_DISPLAY_PLACES = Decimal("0.01")
SCALE_NAMES = {1_000: "thousand", 1_000_000: "million", 1_000_000_000: "billion"}
PERIOD_LENGTH_TOLERANCE_DAYS = 7  # 52/53-week fiscal years differ by a week at most

METRIC_ALIASES: dict[str, tuple[str, ...]] = {
    "revenue": ("revenue", "revenues", "total revenue", "total revenues"),
    "net_income": ("net income", "net earnings"),
    "operating_cash_flow": ("operating cash flow", "operating cash flows", "net cash from operations", "cash from operations",
                            "net cash provided by operating activities", "cash flow from operations"),
}
METRIC_LABELS = {"revenue": "revenue", "net_income": "net income", "operating_cash_flow": "operating cash flow"}
# Metrics we recognise but that this milestone's evidence does not contain.
KNOWN_UNEXTRACTED = ("operating income", "gross margin", "gross profit", "earnings per share", "eps", "free cash flow", "ebitda",
                     "total assets", "total liabilities", "debt", "segment", "dividend", "capital expenditure", "capex")
FORECAST_WORDS = re.compile(r"\b(?:will|forecast\w*|predict\w*|project(?:ed|ion|ions)?|outlook|guidance|expect(?:ed|s|ation|ations)?|"
                            r"anticipat\w+|next\s+year)\b", re.I)


# ------------------------------------------------------------------------------------------------ models


class EvidenceRef(Strict):
    """A cited fact, copied from the fact table with its locator and checksum. Validators re-resolve it against the table."""

    evidence_id: str
    metric: str
    original_label: str
    raw_amount_text: str
    reported_value: Decimal
    scale_multiplier: int
    normalized_value: Decimal
    currency: str
    period_label: str
    period_start: date
    period_end: date
    scope: str
    accounting_basis: str
    accession: str
    source_url: str
    source_checksum: str
    currency_evidence_kind: str
    locator: Locator
    amount_excerpt: str
    supporting_excerpts: list[Excerpt]  # period / scale / scope / basis / currency evidence for this fact

    @classmethod
    def from_fact(cls, f: Fact) -> "EvidenceRef":
        amount = next((e.text for e in f.excerpts if e.kind == "amount"), "")
        return cls(
            evidence_id=f.evidence_id, metric=f.metric, original_label=f.original_label, raw_amount_text=f.raw_amount_text,
            reported_value=f.reported_value, scale_multiplier=f.scale_multiplier, normalized_value=f.normalized_value,
            currency=f.currency, period_label=f.period.label, period_start=f.period.start, period_end=f.period.end, scope=f.scope,
            accounting_basis=f.accounting_basis, accession=f.accession, source_url=f.source_url, source_checksum=f.source_checksum,
            currency_evidence_kind=f.currency_evidence_kind, locator=f.locator, amount_excerpt=amount,
            supporting_excerpts=[e for e in f.excerpts if e.kind != "amount"])


class CalculationInput(Strict):
    role: Literal["prior", "current"]
    evidence_id: str
    normalized_value: Decimal


class CalculationRecord(Strict):
    operation: Literal["absolute_and_percent_change"] = "absolute_and_percent_change"
    formula_absolute: str = "absolute_change = current - prior"
    formula_percent: str = "percent_change = (current - prior) / prior * 100, only when prior > 0"
    inputs: list[CalculationInput]
    absolute_change_usd: Decimal
    percent_change_exact: Decimal | None
    percent_change_display: str | None
    percent_withheld_reason_code: str | None = None
    percent_withheld_reason: str | None = None
    precision_significant_digits: int = PERCENT_PRECISION
    display_rounding: str = "two decimal places, ROUND_HALF_UP"
    steps: list[str]


class LookupValue(Strict):
    metric: str
    period_label: str
    normalized_usd: Decimal
    reported_amount: Decimal
    reported_scale_multiplier: int
    display: str


class ChangeValue(Strict):
    metric: str
    prior_period_label: str
    current_period_label: str
    absolute_change_usd: Decimal
    absolute_change_display: str
    direction: Literal["increase", "decrease", "no_change"]
    percent_change_exact: Decimal | None
    percent_change_display: str | None
    percent_withheld_reason: str | None


class AnswerSource(Strict):
    company: str
    form: str
    accession: str
    url: str
    fiscal_period_end: date
    checksum: str
    source_verified: bool
    verification_problems: list[str]
    # cached_source_verified: the cached filing bytes were re-hashed and matched the manifest just now.
    # reviewed_snapshot_fact_table_only: the filing bytes are NOT present; the fact table's checksum matches the recorded hash of
    #   the previously reviewed snapshot. Weaker, and always labelled as such.
    verification_level: Literal["cached_source_verified", "reviewed_snapshot_fact_table_only", "unverified"] = "unverified"


class Answer(Strict):
    answer_schema_version: int = ANSWER_SCHEMA_VERSION
    question: str
    question_kind: Literal["lookup", "change", "unsupported"]
    status: Literal["answered", "unresolved", "refused"]
    entity: str
    value: LookupValue | ChangeValue | None = None
    evidence: list[EvidenceRef]
    calculation: CalculationRecord | None = None
    reason_code: str | None = None
    reason: str | None = None
    limitations: list[str]
    source: AnswerSource

    @model_validator(mode="after")
    def _shape(self) -> "Answer":
        if self.status == "answered":
            if self.value is None or not self.evidence or self.reason_code or self.reason:
                raise ValueError("an answered answer needs a value and evidence and no reason code")
            if self.question_kind == "lookup" and (len(self.evidence) != 1 or self.calculation is not None):
                raise ValueError("a lookup cites exactly one fact and has no calculation")
            if self.question_kind == "change" and (len(self.evidence) != 2 or self.calculation is None):
                raise ValueError("a change cites exactly two facts and carries a calculation record")
            if self.question_kind == "unsupported":
                raise ValueError("an unsupported question cannot be answered")
        else:
            if self.value is not None or self.evidence or self.calculation is not None:
                raise ValueError("an unresolved or refused answer must carry no value, no evidence and no calculation")
            if not self.reason_code or not self.reason:
                raise ValueError("an unresolved or refused answer needs a reason code and a reason")
        return self


# ------------------------------------------------------------------------------------------------ the verified fact book


class FactBookError(Exception):
    """The fact table could not be read as a fact table at all."""


@dataclass
class FactBook:
    table: FactTable
    verified: bool
    problems: list[str] = field(default_factory=list)
    level: str = "unverified"

    @classmethod
    def from_reviewed_snapshot(cls, table: FactTable, snapshot: dict[str, Any]) -> "FactBook":
        """Weaker mode for a clean clone that has the reviewed fact table but not the filing bytes: the table must carry the hash and
        accession of the reviewed snapshot recorded in `data/reviewed_snapshot.json`. Answers are labelled `reviewed_snapshot_fact_table_only`."""
        problems: list[str] = []
        if table.source.checksum != snapshot.get("sha256"):
            problems.append("table_checksum_differs_from_reviewed_snapshot")
        if table.source.accession != snapshot.get("accession"):
            problems.append("table_accession_differs_from_reviewed_snapshot")
        for f in table.facts:
            if f.source_checksum != table.source.checksum or f.locator.source_checksum != table.source.checksum:
                problems.append(f"fact_checksum_mismatch:{f.evidence_id}")
            if f.accession != table.source.accession:
                problems.append(f"fact_accession_mismatch:{f.evidence_id}")
        return cls(table=table, verified=not problems, problems=problems,
                   level="reviewed_snapshot_fact_table_only" if not problems else "unverified")

    @classmethod
    def from_table(cls, table: FactTable, source_check: dict[str, Any] | None = None, expected_accession: str | None = None) -> "FactBook":
        """Wrap a fact table. `source_check` is `ingest.verify_cached_source(...)` output; without a passing one the book is unverified."""
        problems: list[str] = []
        if source_check is None:
            problems.append("source_not_verified")
        elif not source_check.get("verified"):
            problems += [f"source:{p}" for p in source_check.get("problems", ["unknown"])]
        else:
            actual = (source_check.get("actual") or {}).get("sha256")
            if table.source.checksum != actual:
                problems.append("table_checksum_differs_from_verified_file")
        if expected_accession is not None and table.source.accession != expected_accession:
            problems.append("table_accession_differs_from_manifest")
        for f in table.facts:
            if f.source_checksum != table.source.checksum or f.locator.source_checksum != table.source.checksum:
                problems.append(f"fact_checksum_mismatch:{f.evidence_id}")
            if f.accession != table.source.accession:
                problems.append(f"fact_accession_mismatch:{f.evidence_id}")
        return cls(table=table, verified=not problems, problems=problems, level="cached_source_verified" if not problems else "unverified")

    def facts_for(self, metric: str, period_label: str) -> list[Fact]:
        return [f for f in self.table.facts if f.metric == metric and f.period.label == period_label]

    def facts_by_id(self, evidence_id: str) -> list[Fact]:
        return [f for f in self.table.facts if f.evidence_id == evidence_id]

    @property
    def company(self) -> str:
        return self.table.source.company

    @property
    def latest_fiscal_year(self) -> int | None:
        return max((f.period.end.year for f in self.table.facts), default=None)

    def source(self) -> AnswerSource:
        s = self.table.source
        return AnswerSource(company=s.company, form=s.form, accession=s.accession, url=s.url, fiscal_period_end=s.fiscal_period_end,
                            checksum=s.checksum, source_verified=self.verified, verification_problems=list(self.problems),
                            verification_level=self.level if self.verified else "unverified")


def _load_snapshot(root: Path) -> dict[str, Any] | None:
    path = root / "data" / "reviewed_snapshot.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except (ValueError, OSError):
        return None


def load_factbook(root: Path, *, allow_snapshot: bool = False) -> FactBook:
    """Read `output/fact_table.json` under `root` and re-verify its source NOW against the manifest and the cached bytes.

    With `allow_snapshot=True`, if (and only if) the cached filing is simply ABSENT and `data/reviewed_snapshot.json` exists, fall back
    to the weaker, explicitly labelled `reviewed_snapshot_fact_table_only` mode. A cache that is present but wrong is never excused."""
    from finreport.ingest import verify_cached_source
    from finreport.manifest import load_manifest

    path = root / "output" / "fact_table.json"
    try:
        table = FactTable.model_validate_json(path.read_text(encoding="utf-8"))
    except FileNotFoundError as err:
        raise FactBookError(f"no fact table at {path}; run `finreport extract` first") from err
    except (ValidationError, ValueError) as err:
        raise FactBookError(f"{path.name} is not a valid fact table: {err}") from err
    manifest_path = root / "data" / "source_manifest.json"
    snapshot = _load_snapshot(root) if allow_snapshot else None
    if not manifest_path.exists():
        return FactBook.from_reviewed_snapshot(table, snapshot) if snapshot else FactBook.from_table(table, source_check=None)
    manifest = load_manifest(manifest_path)
    check = verify_cached_source(root, manifest)
    if snapshot and check["problems"] in (["cached_file_missing"], ["no_recorded_source"]):
        return FactBook.from_reviewed_snapshot(table, snapshot)
    return FactBook.from_table(table, source_check=check, expected_accession=manifest.target.accession)


# ------------------------------------------------------------------------------------------------ validators


def fact_integrity_problems(f: Fact, book: FactBook) -> list[str]:
    """Reasons a single fact must not be used to answer, whatever the question."""
    out: list[str] = []
    if f.normalized_value != f.reported_value * f.scale_multiplier:
        out.append("normalized_value_inconsistent_with_reported_value_and_scale")
    if f.scale_multiplier not in SCALE_NAMES:
        out.append("unsupported_scale_multiplier")
    if f.scope != "consolidated":
        out.append("scope_not_consolidated")
    if f.currency != "USD":
        out.append("currency_not_usd")
    if f.period.type != "annual_duration":
        out.append("period_not_annual")
    if f.conflict_status != "none":
        out.append("fact_flagged_with_conflict")
    if any((not c.resolved) and c.metric == f.metric and c.period_label == f.period.label for c in book.table.conflicts):
        out.append("unresolved_conflict_for_metric_period")
    if any(i.blocking and i.metric == f.metric and i.period_label in (f.period.label, None) for i in book.table.issues):
        out.append("blocking_extraction_issue_for_metric_period")
    if f.source_checksum != book.table.source.checksum or f.accession != book.table.source.accession:
        out.append("fact_source_differs_from_table_source")
    return out


def validate_comparable(prior: Fact, current: Fact) -> list[str]:
    """Reasons two facts must not be compared (empty list = comparable). Different filings are never silently combined."""
    out: list[str] = []
    if prior.metric != current.metric:
        out.append("metric_mismatch")
    if prior.scope != current.scope:
        out.append("scope_mismatch")
    if prior.accounting_basis != current.accounting_basis:
        out.append("accounting_basis_mismatch")
    if prior.currency != current.currency:
        out.append("currency_mismatch")
    if (prior.accession, prior.source_checksum) != (current.accession, current.source_checksum):
        out.append("source_filing_mismatch")
    if prior.period.type != "annual_duration" or current.period.type != "annual_duration":
        out.append("period_not_annual")
    if prior.period.end == current.period.end:
        out.append("same_period")
    days = lambda f: (f.period.end - f.period.start).days + 1  # noqa: E731
    if abs(days(prior) - days(current)) > PERIOD_LENGTH_TOLERANCE_DAYS:
        out.append("period_length_incomparable")
    return out


def percent_change(prior: Decimal, current: Decimal) -> tuple[Decimal | None, str | None, str | None]:
    """(percent, withheld_reason_code, withheld_reason). Percent only for a positive prior amount."""
    if prior == 0:
        return None, "prior_period_zero", ("Percentage change is withheld because the prior-period amount is zero, so the ratio is "
                                           "undefined. The absolute change is reported.")
    if prior < 0:
        return None, "prior_period_negative", ("Percentage change is withheld because the prior-period amount is negative, so a "
                                               "percentage of a negative base is not meaningful. The absolute change is reported.")
    with localcontext(Context(prec=PERCENT_PRECISION, rounding=ROUND_HALF_EVEN)):
        return (current - prior) / prior * 100, None, None


def percent_display(value: Decimal) -> str:
    with localcontext(Context(prec=80)):
        return f"{value.quantize(PERCENT_DISPLAY_PLACES, rounding=ROUND_HALF_UP)}%"


def format_usd(normalized: Decimal, scale: int | None) -> str:
    """'USD 1,000 million'-style text when a standard scale is given, else full dollars."""
    sign = "-" if normalized < 0 else ""
    amount = abs(normalized)
    if scale in SCALE_NAMES:
        shown = amount / Decimal(scale)
        shown = shown.to_integral_value() if shown == shown.to_integral_value() else shown
        return f"{sign}USD {format(shown, ',f')} {SCALE_NAMES[scale]}"
    return f"{sign}USD {format(amount, ',f')}"


def build_calculation(prior: Fact, current: Fact) -> CalculationRecord:
    p, c = prior.normalized_value, current.normalized_value
    diff = c - p
    pct, code, why = percent_change(p, c)
    steps = [f"absolute_change = current - prior = {c} - {p} = {diff}"]
    if pct is not None:
        steps.append(f"percent_change = (current - prior) / prior * 100 = ({c} - {p}) / {p} * 100 = {pct}")
        display = percent_display(pct)
        steps.append(f"displayed to two decimal places, ROUND_HALF_UP = {display}")
    else:
        display = None
        steps.append(f"percent_change withheld ({code})")
    return CalculationRecord(
        inputs=[CalculationInput(role="prior", evidence_id=prior.evidence_id, normalized_value=p),
                CalculationInput(role="current", evidence_id=current.evidence_id, normalized_value=c)],
        absolute_change_usd=diff, percent_change_exact=pct, percent_change_display=display,
        percent_withheld_reason_code=code, percent_withheld_reason=why, steps=steps)


def validate_calculation(record: CalculationRecord) -> list[str]:
    """Independently recompute a calculation record from its inputs and report every disagreement."""
    out: list[str] = []
    roles = {i.role: i for i in record.inputs}
    if set(roles) != {"prior", "current"} or len(record.inputs) != 2:
        return ["inputs_must_be_exactly_one_prior_and_one_current"]
    p, c = roles["prior"].normalized_value, roles["current"].normalized_value
    if record.absolute_change_usd != c - p:
        out.append("absolute_change_does_not_equal_current_minus_prior")
    pct, code, _ = percent_change(p, c)
    if pct is None:
        if record.percent_change_exact is not None or record.percent_change_display is not None:
            out.append("percent_present_but_prior_amount_is_not_positive")
        if record.percent_withheld_reason_code != code or not record.percent_withheld_reason:
            out.append("percent_withheld_without_the_correct_reason")
    else:
        if record.percent_change_exact != pct:
            out.append("percent_change_does_not_match_recomputation")
        if record.percent_change_display != percent_display(pct):
            out.append("percent_display_does_not_match_rounding_rule")
        if record.percent_withheld_reason_code or record.percent_withheld_reason:
            out.append("percent_has_a_withheld_reason")
    return out


def validate_answer(answer: Answer, book: FactBook, expected: list[tuple[str, str]] | None = None) -> list[str]:
    """Re-check an answer against the fact book: every cited reference must resolve to exactly one fact whose copied fields all
    match; the cited metric/periods must be the ones asked for (`expected` = [(metric, period_label), ...]); a calculation must
    recompute from the cited values only; unresolved/refused answers must cite nothing. Empty list = valid."""
    out: list[str] = []
    if answer.status != "answered":
        if answer.evidence or answer.value is not None or answer.calculation is not None:
            out.append("non_answer_carries_evidence_value_or_calculation")
        return out
    seen: set[str] = set()
    for ref in answer.evidence:
        if ref.evidence_id in seen:
            out.append(f"duplicate_evidence_reference:{ref.evidence_id}")
        seen.add(ref.evidence_id)
        matches = book.facts_by_id(ref.evidence_id)
        if len(matches) != 1:
            out.append(f"evidence_reference_does_not_resolve_to_one_fact:{ref.evidence_id}")
            continue
        truth = EvidenceRef.from_fact(matches[0])
        for name in EvidenceRef.model_fields:
            if getattr(ref, name) != getattr(truth, name):
                out.append(f"evidence_field_differs_from_fact_table:{ref.evidence_id}:{name}")
    if expected is not None:
        got = sorted((r.metric, r.period_label) for r in answer.evidence)
        if got != sorted(expected):
            out.append(f"cited_evidence_is_not_the_requested_metric_and_periods:{got}")
    if answer.calculation is not None:
        out += validate_calculation(answer.calculation)
        cited = {r.evidence_id: r for r in answer.evidence}
        if {i.evidence_id for i in answer.calculation.inputs} != set(cited):
            out.append("calculation_inputs_are_not_exactly_the_cited_evidence")
        for i in answer.calculation.inputs:
            if i.evidence_id in cited and cited[i.evidence_id].normalized_value != i.normalized_value:
                out.append(f"calculation_input_differs_from_cited_value:{i.evidence_id}")
        if isinstance(answer.value, ChangeValue):
            if answer.value.absolute_change_usd != answer.calculation.absolute_change_usd:
                out.append("answer_value_differs_from_calculation")
            if (answer.value.percent_change_exact, answer.value.percent_change_display) != (
                    answer.calculation.percent_change_exact, answer.calculation.percent_change_display):
                out.append("answer_percent_differs_from_calculation")
    if isinstance(answer.value, LookupValue) and answer.evidence:
        ref = answer.evidence[0]
        if (answer.value.normalized_usd, answer.value.reported_amount, answer.value.reported_scale_multiplier) != (
                ref.normalized_value, ref.reported_value, ref.scale_multiplier):
            out.append("lookup_value_differs_from_cited_evidence")
    return out


class AnswerValidationError(Exception):
    pass


# ------------------------------------------------------------------------------------------------ question parsing


@dataclass(frozen=True)
class Question:
    text: str
    kind: Literal["lookup", "change", "unsupported"]
    metric: str | None = None
    fiscal_years: tuple[int, ...] = ()
    reason_code: str | None = None
    reason: str | None = None


def _normalize(text: str) -> str:
    t = text.strip().lower().replace("’", "'").replace("‘", "'")
    t = re.sub(r"[?.!]+$", "", t)
    return re.sub(r"\s+", " ", t)


def _entity_pattern(company: str) -> str:
    base = re.sub(r"\b(?:corporation|corp\.?|incorporated|inc\.?|company|co\.?)\s*$", "", company.strip(), flags=re.I).strip()
    names = {re.escape(n.lower()) for n in (company, base) if n} | {r"the\s+company"}
    return rf"(?:(?:{'|'.join(sorted(names, key=len, reverse=True))})(?:'s)?\s+)?"


def parse_question(text: str, company: str) -> Question:
    q = _normalize(text)
    aliases = sorted(((a, k) for k, v in METRIC_ALIASES.items() for a in v), key=lambda x: -len(x[0]))
    alias_to_key = dict(aliases)
    metric = rf"(?P<metric>{'|'.join(re.escape(a) for a, _ in aliases)})"
    fy = lambda name: rf"(?:fy\s?|fiscal\s+(?:year\s+)?)(?P<{name}>\d{{4}})"  # noqa: E731
    ent = _entity_pattern(company)
    core = rf"(?:the\s+)?{ent}(?:consolidated\s+)?(?:total\s+)?{metric}"
    patterns = (
        ("lookup", rf"^what\s+(?:was|were|is)\s+{core}\s+(?:in|for|during)\s+{fy('y1')}$"),
        ("change", rf"^how\s+much\s+did\s+{core}\s+change\s+(?:from|between)\s+{fy('y1')}\s+(?:to|and)\s+{fy('y2')}$"),
        ("change", rf"^what\s+(?:was|is)\s+the\s+(?:change|difference)\s+in\s+{ent}(?:consolidated\s+)?(?:total\s+)?{metric}\s+"
                   rf"(?:from|between)\s+{fy('y1')}\s+(?:to|and)\s+{fy('y2')}$"),
        ("forecast", rf"^what\s+will\s+{core}\s+be\s+(?:in|for)\s+{fy('y1')}$"),
    )
    for kind, pattern in patterns:
        m = re.match(pattern, q)
        if not m:
            continue
        years = tuple(int(m.group(n)) for n in ("y1", "y2") if m.groupdict().get(n))
        key = alias_to_key[m.group("metric")]
        if kind == "forecast":
            return Question(text, "unsupported", key, years, "forecast_not_supported", None)
        return Question(text, kind, key, years)  # type: ignore[arg-type]
    if FORECAST_WORDS.search(q):
        return Question(text, "unsupported", None, (), "forecast_not_supported", None)
    if any(re.search(rf"\b{re.escape(w)}\b", q) for w in KNOWN_UNEXTRACTED):
        return Question(text, "unsupported", None, (), "metric_not_in_evidence", None)
    return Question(text, "unsupported", None, (), "unsupported_question", None)


# ------------------------------------------------------------------------------------------------ answering


def _limitations(book: FactBook) -> list[str]:
    s = book.table.source
    return [f"Values are as presented in the selected historical {s.form} (accession {s.accession}) for the fiscal year ended "
            f"{s.fiscal_period_end}. A search for later amendments (10-K/A) has not been performed, so these are not claimed to be "
            "the latest-amended values.",
            "Consolidated, in USD; each cited fact keeps its own accounting basis and scale."]


def _non_answer(q: Question, book: FactBook, status: str, code: str, reason: str, kind: str | None = None) -> Answer:
    return Answer(question=q.text, question_kind=kind or q.kind, status=status, entity=book.company, evidence=[],  # type: ignore[arg-type]
                  reason_code=code, reason=reason, limitations=_limitations(book), source=book.source())


def _unsupported_reason(q: Question, book: FactBook) -> str:
    latest = book.latest_fiscal_year
    if q.reason_code == "forecast_not_supported":
        target = f" for FY{q.fiscal_years[0]}" if q.fiscal_years else ""
        what = METRIC_LABELS.get(q.metric or "", "the requested figure")
        return (f"The selected historical filing's evidence covers fiscal years up to FY{latest}; it does not establish {what}{target}. "
                "No forecast is made, and no evidence is cited for it.")
    if q.reason_code == "metric_not_in_evidence":
        return ("That figure is not among the facts extracted from the selected filing. Supported metrics: "
                + ", ".join(METRIC_LABELS.values()) + ".")
    return ("This question is outside the supported forms. Supported: 'What was <metric> in FY<year>?' and 'How much did <metric> "
            "change from FY<year> to FY<year>?', for " + ", ".join(METRIC_LABELS.values()) + ".")


def _resolve(book: FactBook, metric: str, fy: int, q: Question) -> tuple[Fact | None, Answer | None]:
    """The single usable fact for (metric, fiscal year), or the non-answer explaining why there is none."""
    latest = book.latest_fiscal_year
    label = f"FY{fy}"
    if latest is not None and fy > latest:
        return None, _non_answer(q, book, "unresolved", "forecast_not_supported",
                                 f"{label} ends after the selected filing's evidence (latest evidenced year FY{latest}); the historical "
                                 "evidence does not establish it. No forecast is made.")
    facts = book.facts_for(metric, label)
    if not facts:
        has_metric = any(f.metric == metric for f in book.table.facts)
        code = "period_not_in_evidence" if has_metric else "metric_not_in_evidence"
        return None, _non_answer(q, book, "unresolved", code, f"No {METRIC_LABELS[metric]} fact for {label} was extracted from the selected filing.")
    if len(facts) > 1:
        return None, _non_answer(q, book, "unresolved", "ambiguous_evidence",
                                 f"{len(facts)} facts match {METRIC_LABELS[metric]} {label}; the evidence is ambiguous.")
    problems = fact_integrity_problems(facts[0], book)
    if problems:
        return None, _non_answer(q, book, "refused", "evidence_not_verified",
                                 f"The {METRIC_LABELS[metric]} {label} fact is not usable: {', '.join(problems)}.")
    return facts[0], None


def answer_question(text: str, book: FactBook) -> Answer:
    q = parse_question(text, book.company)
    if q.kind == "unsupported":
        return _non_answer(q, book, "unresolved", q.reason_code or "unsupported_question", _unsupported_reason(q, book))
    if not book.verified:
        return _non_answer(q, book, "refused", "source_not_verified",
                           "The evidence is not verified against the cached source: " + ", ".join(book.problems) + ".")
    assert q.metric is not None
    if q.kind == "lookup":
        fact, refusal = _resolve(book, q.metric, q.fiscal_years[0], q)
        if refusal is not None:
            return refusal
        assert fact is not None
        ans = Answer(question=text, question_kind="lookup", status="answered", entity=book.company, evidence=[EvidenceRef.from_fact(fact)],
                     value=LookupValue(metric=fact.metric, period_label=fact.period.label, normalized_usd=fact.normalized_value,
                                       reported_amount=fact.reported_value, reported_scale_multiplier=fact.scale_multiplier,
                                       display=format_usd(fact.normalized_value, fact.scale_multiplier)),
                     limitations=_limitations(book), source=book.source())
        expected = [(q.metric, fact.period.label)]
    else:
        prior, refusal = _resolve(book, q.metric, q.fiscal_years[0], q)
        if refusal is not None:
            return refusal
        current, refusal = _resolve(book, q.metric, q.fiscal_years[1], q)
        if refusal is not None:
            return refusal
        assert prior is not None and current is not None
        problems = validate_comparable(prior, current)
        if problems:
            return _non_answer(q, book, "refused", "facts_not_comparable",
                               f"{prior.evidence_id} and {current.evidence_id} cannot be compared: {', '.join(problems)}.")
        calc = build_calculation(prior, current)
        diff = calc.absolute_change_usd
        scale = prior.scale_multiplier if prior.scale_multiplier == current.scale_multiplier else None
        ans = Answer(
            question=text, question_kind="change", status="answered", entity=book.company,
            evidence=[EvidenceRef.from_fact(prior), EvidenceRef.from_fact(current)], calculation=calc,
            value=ChangeValue(metric=current.metric, prior_period_label=prior.period.label, current_period_label=current.period.label,
                              absolute_change_usd=diff, absolute_change_display=format_usd(diff, scale),
                              direction="increase" if diff > 0 else "decrease" if diff < 0 else "no_change",
                              percent_change_exact=calc.percent_change_exact, percent_change_display=calc.percent_change_display,
                              percent_withheld_reason=calc.percent_withheld_reason),
            limitations=_limitations(book), source=book.source())
        expected = [(q.metric, prior.period.label), (q.metric, current.period.label)]
    problems = validate_answer(ans, book, expected)
    if problems:  # defence in depth: an answer that fails its own validators is never returned as an answer
        raise AnswerValidationError("; ".join(problems))
    return ans


def answer_to_json(answer: Answer) -> str:
    return json.dumps(json.loads(answer.model_dump_json()), indent=2) + "\n"
