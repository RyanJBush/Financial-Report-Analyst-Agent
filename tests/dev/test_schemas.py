from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from finreport.schemas import EXCERPT_KINDS, Excerpt, Fact, Locator, Period

SHA = "cd" * 32


def locator(**kw):
    base = dict(statement="INCOME STATEMENTS", row="Total revenue", column="2024", table_index=0, row_index=2, cell_index=3,
                header_row_index=1, header_cell_index=1, source_checksum=SHA)
    return Locator(**{**base, **kw})


def fact(**kw):
    base = dict(
        evidence_id="REV_2024", metric="revenue", original_label="Total revenue", raw_amount_text="$1,000",
        reported_value=Decimal(1000), currency="USD", currency_evidence_kind="statement_header_usd", scale_multiplier=1_000_000, normalized_value=Decimal(1_000_000_000),
        period=Period(label="FY2024", start=date(2023, 7, 1), end=date(2024, 6, 30)), scope="consolidated",
        scope_description="x", accounting_basis="US GAAP", accession="0001234567-24-000001", source_url="https://x",
        source_checksum=SHA, locator=locator(), excerpts=[Excerpt(kind=k, text="t", where="w") for k in EXCERPT_KINDS],
    )
    return Fact(**{**base, **kw})


def test_valid_fact_roundtrips():
    f = fact()
    assert Fact.model_validate_json(f.model_dump_json()) == f


def test_normalization_must_equal_value_times_scale():
    with pytest.raises(ValidationError, match="normalized_value"):
        fact(normalized_value=Decimal(1_000_000))


def test_unsupported_scale_rejected():
    with pytest.raises(ValidationError, match="scale"):
        fact(scale_multiplier=7, normalized_value=Decimal(7000))


def test_every_excerpt_kind_is_required():
    with pytest.raises(ValidationError, match="missing supporting excerpts"):
        fact(excerpts=[Excerpt(kind="amount", text="t", where="w")])


def test_checksum_and_accession_formats_enforced():
    with pytest.raises(ValidationError):
        fact(source_checksum="nothex")
    with pytest.raises(ValidationError):
        fact(accession="12345")


def test_locator_checksum_must_match_fact_checksum():
    with pytest.raises(ValidationError, match="locator checksum"):
        fact(locator=locator(source_checksum="ef" * 32))


@pytest.mark.parametrize("start,end", [(date(2024, 4, 1), date(2024, 6, 30)), (date(2023, 7, 1), date(2025, 6, 30))])
def test_period_must_be_one_complete_year(start, end):
    with pytest.raises(ValidationError, match="annual"):
        Period(label="x", start=start, end=end)


def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        Excerpt(kind="amount", text="t", where="w", surprise=1)


def test_currency_evidence_kind_is_required_and_constrained():
    with pytest.raises(ValidationError):
        fact(currency_evidence_kind="assumed")
    base = fact().model_dump()
    del base["currency_evidence_kind"]
    with pytest.raises(ValidationError):
        Fact(**base)
