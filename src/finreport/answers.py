"""Bounded questions, exact Decimal calculations, and evidence validation.

Production code never reads evaluation expectations. Returned numbers are base currency
units; display scales are applied only by the renderer. No forecasts or causal claims.
"""
from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from finreport.ingest import verify_cached_source
from finreport.manifest import load_manifest
from finreport.schemas import Fact, FactTable

METRICS = {'revenue', 'net income', 'operating cash flow'}
SCOPE_NOTE = ('As presented in the selected filing; a search for later 10-K/A amendments '
              'has not been completed. No forecasts or causal explanations are inferred.')


def calculate_change(before: Fact, after: Fact) -> dict:
    """Compare consecutive annual periods; percentage requires a positive prior amount."""
    for field in ('metric','scope','accounting_basis','currency','accession','source_checksum'):
        if getattr(before,field) != getattr(after,field):
            raise ValueError(f'incomparable {field}')
    if (after.period.start - before.period.end).days != 1:
        raise ValueError('periods must be consecutive and chronological')
    duration_delta = abs((after.period.end-after.period.start).days - (before.period.end-before.period.start).days)
    if duration_delta > 7:
        raise ValueError('annual durations differ by more than seven days')
    for f in (before,after):
        if not f.normalized_value.is_finite() or f.normalized_value != f.reported_value * f.scale_multiplier:
            raise ValueError('invalid normalized value')
    delta = after.normalized_value-before.normalized_value
    percent, exact, reason = None, None, None
    if before.normalized_value > 0:
        exact = str(delta / before.normalized_value * 100)
        percent = str(Decimal(exact).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP))
    else:
        reason = 'zero_prior_amount' if before.normalized_value == 0 else 'negative_prior_amount'
    return {'calculation_id':'CALC_CHANGE','evidence_ids':[before.evidence_id,after.evidence_id],
            'prior_value':str(before.normalized_value),'current_value':str(after.normalized_value),
            'absolute_change':str(delta),'currency':before.currency,'units':'base_currency_units',
            'percent_change':percent,'percent_exact':exact,'percent_reason':reason,
            'formula':'(current - prior) / prior * 100; percent only when prior > 0',
            'rounding':'ROUND_HALF_UP, 2 decimal places'}


def _parse(question: str):
    q = ' '.join(question.lower().strip().rstrip('?').split())
    lookup = re.fullmatch(r'what was (revenue|net income|operating cash flow) in (fy\d{4})',q)
    change = re.fullmatch(r'how did (revenue|net income|operating cash flow) change from (fy\d{4}) to (fy\d{4})',q)
    if lookup: return lookup[1].replace(' ','_'),[lookup[2].upper()]
    if change: return change[1].replace(' ','_'),[change[2].upper(),change[3].upper()]
    return None


def _claim(f: Fact) -> dict:
    return {'evidence_id':f.evidence_id,'metric':f.metric,'value':str(f.normalized_value),
            'currency':f.currency,'period':f.period.label,'scope':f.scope,'basis':f.accounting_basis}


def answer_question(question: str, table: FactTable) -> dict:
    answer = {'question':question,'status':'unresolved','reason':'unsupported_question',
              'claims':[],'evidence_ids':[],'calculation':None,'scope_note':SCOPE_NOTE}
    if re.fullmatch(r'what will (revenue|net income|operating cash flow) be in fy\d{4}\??',question.lower().strip()):
        answer['reason'] = 'forecast_not_supported'
        return answer
    request = _parse(question)
    if request is None: return answer
    metric,years = request
    if any(int(y[2:]) > table.source.fiscal_period_end.year for y in years):
        answer['reason'] = 'forecast_not_supported'
        return answer
    if any(i.blocking for i in table.issues) or any(not c.resolved for c in table.conflicts):
        answer['reason'] = 'conflicting_or_blocking_evidence'
        return answer
    selected = []
    for year in years:
        matches = [f for f in table.facts if f.metric == metric and f.period.label == year
                   and f.scope == 'consolidated' and f.accounting_basis == 'US GAAP'
                   and f.currency == 'USD' and f.source_checksum == table.source.checksum
                   and f.accession == table.source.accession and f.conflict_status == 'none']
        if len(matches) != 1:
            answer['reason'] = 'missing_or_ambiguous_evidence'
            return answer
        selected.append(matches[0])
    if len({f.evidence_id for f in table.facts}) != len(table.facts):
        answer['reason'] = 'missing_or_ambiguous_evidence'
        return answer
    try:
        calculation = calculate_change(*selected) if len(selected) == 2 else None
    except ValueError:
        answer['reason'] = 'incomparable_periods'
        return answer
    answer.update(status='answered',reason=None,claims=[_claim(f) for f in selected],
                  evidence_ids=[f.evidence_id for f in selected],calculation=calculation)
    return answer


def validate_answer(answer: dict, table: FactTable) -> bool:
    """Recompute from the question; reject wrong years, values, citations, and calculations."""
    try:
        return answer == answer_question(answer['question'],table)
    except (KeyError,TypeError,ValueError):
        return False


def load_verified_table(root: Path) -> FactTable:
    manifest = load_manifest(root/'data/source_manifest.json')
    verification = verify_cached_source(root,manifest)
    if not verification['verified']:
        raise ValueError('source_verification_failed: '+','.join(verification['problems']))
    table = FactTable.model_validate_json((root/'output/fact_table.json').read_text(encoding='utf-8'))
    source = table.source
    for field in ('company','cik','form','accession','fiscal_period_end','filing_date'):
        if getattr(source,field) != getattr(manifest.target,field):
            raise ValueError('filing_identity_mismatch')
    if source.url != manifest.target.url or source.checksum != verification['actual']['sha256']:
        raise ValueError('source_table_mismatch')
    if any(f.source_checksum != source.checksum or f.source_url != source.url or f.accession != source.accession
           for f in table.facts):
        raise ValueError('fact_source_mismatch')
    return table
