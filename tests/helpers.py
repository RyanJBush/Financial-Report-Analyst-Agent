"""Synthetic filing builder. Fictional company; numbers deliberately differ from the frozen expectations."""

from __future__ import annotations

from datetime import date

from finreport.manifest import Target

TARGET = Target(
    company="Examplecorp",
    cik="0000000001",
    form="10-K",
    accession="0001234567-24-000001",
    fiscal_period_end=date(2024, 6, 30),
    filing_date=date(2024, 7, 30),
    url="https://www.sec.gov/Archives/edgar/data/1/000123456724000001/exam-20240630.htm",
    index_url="https://www.sec.gov/Archives/edgar/data/1/000123456724000001/0001234567-24-000001-index.htm",
)
CHECKSUM = "ab" * 32

CONSOLIDATION_TEXT = (
    "The consolidated financial statements include the accounts of Examplecorp and its subsidiaries. "
    "Intercompany transactions have been eliminated."
)
GAAP_TEXT = (
    "The consolidated financial statements have been prepared in accordance with accounting principles "
    "generally accepted in the United States of America (GAAP)."
)


def _num_cells(value, dollar: bool) -> str:
    """One logical amount rendered Workiva-style: optional '$' cell, number cell, optional ')' cell."""
    if value is None:
        return "<td></td>"
    ix = None
    if isinstance(value, tuple):
        value, ix = value
    text = value
    neg = text.startswith("(") and text.endswith(")")
    core = text[1:-1] if neg else text
    if ix is not None:
        attrs = " ".join(f'{k}="{v}"' for k, v in ix.items())
        core_html = f"<ix:nonFraction {attrs}>{core}</ix:nonFraction>"
    else:
        core_html = core
    out = "<td>$</td>" if dollar else ""
    if neg:
        out += f"<td>({core_html}</td><td>)</td>" if ix is None else f"<td>(</td><td>{core_html}</td><td>)</td>"
    else:
        out += f"<td>{core_html}</td>"
    return out


def row(label: str, values: list, dollar: bool = False, row_id: str | None = None) -> str:
    rid = f' id="{row_id}"' if row_id else ""
    cells = "".join("<td></td>" + _num_cells(v, dollar) for v in values)
    return f"<tr{rid}><td>{label}</td>{cells}</tr>"


def statement(
    title: str | None,
    rows: list[str],
    *,
    years: tuple[str, ...] = ("2024", "2023"),
    scale: str | None = "(In millions, except per share amounts)",
    period_phrase: str | None = "Year Ended June 30,",
    page: int | None = None,
    table_id: str | None = None,
    header_style: str = "two_rows",
    extra_header_rows: str = "",
) -> str:
    parts = []
    if title:
        parts.append(f"<div>{title}</div>")
    if scale:
        parts.append(f"<div>{scale}</div>")
    tid = f' id="{table_id}"' if table_id else ""
    head = ""
    if header_style == "two_rows":  # period label row above a years-only row (original layout)
        if period_phrase:
            head += f'<tr><td></td><td colspan="{len(years) * 2}">{period_phrase}</td></tr>'
        head += "<tr><td></td>" + "".join(f"<td>{y}</td><td></td>" for y in years) + "</tr>"
    elif header_style == "label_in_year_row":  # "Year Ended June 30," | 2024 | 2023 in ONE row
        head += f"<tr><td>{period_phrase or ''}</td>" + "".join(f"<td>{y}</td><td></td>" for y in years) + "</tr>"
    else:
        raise ValueError(header_style)
    head += extra_header_rows
    parts.append(f"<table{tid}>{head}{''.join(rows)}</table>")
    if page is not None:
        parts.append(f'<div>{page}</div><hr style="page-break-after:always"/>')
    return "\n".join(parts)


def income(rows=None, **kw) -> str:
    rows = rows or [row("Total revenue", ["1,000", "900"], dollar=True), row("Net income", ["250", "200"], dollar=True)]
    return statement(kw.pop("title", "INCOME STATEMENTS"), rows, page=kw.pop("page", 3), **kw)


def comprehensive(rows=None, **kw) -> str:
    rows = rows or [row("Net income", ["250", "200"], dollar=True)]
    return statement(kw.pop("title", "COMPREHENSIVE INCOME STATEMENTS"), rows, page=kw.pop("page", 4), **kw)


def cashflow(rows=None, **kw) -> str:
    rows = rows or [
        row("Net income", ["250", "200"], dollar=True),
        row("Net cash from operations", ["400", "350"]),
    ]
    return statement(kw.pop("title", "CASH FLOWS STATEMENTS"), rows, page=kw.pop("page", 5), **kw)


CURRENCY_TEXT = "The consolidated financial statements are presented in U.S. dollars."
IX_NAMESPACES = (' xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" xmlns:xbrli="http://www.xbrl.org/2003/instance"'
                 ' xmlns:iso4217="http://www.xbrl.org/2003/iso4217"')


def unit(uid: str, measure: str = "iso4217:USD") -> str:
    return f'<xbrli:unit id="{uid}"><xbrli:measure>{measure}</xbrli:measure></xbrli:unit>'


def ix_header(*units: str) -> str:
    return f'<div style="display:none"><ix:header><ix:resources>{"".join(units)}</ix:resources></ix:header></div>'


def ixrow(label: str, values: list, unit_ref: str | None = "usd", dollar: bool = True) -> str:
    """A statement row whose amounts are inline-XBRL tagged (scale 6 = millions) with the given unitRef."""
    tagged = []
    for v in values:
        attrs = {"name": "us-gaap:Revenues", "scale": "6", "contextRef": "c"}
        if unit_ref is not None:
            attrs["unitRef"] = unit_ref
        tagged.append((v, attrs))
    return row(label, tagged, dollar=dollar)


def make_doc(*tables: str, consolidation: bool = True, gaap: bool = True, currency: bool = True,
             html_attrs: str = "", header: str = "") -> bytes:
    """Synthetic filing. By default it carries genuine evidence for scope, GAAP basis AND reporting currency
    (a document sentence tying the financial statements to U.S. dollars). Pass currency=False for a document with
    no governing currency evidence (the negative case)."""
    prose = ""
    if consolidation:
        prose += f"<p>{CONSOLIDATION_TEXT}</p>"
    if gaap:
        prose += f"<p>{GAAP_TEXT}</p>"
    if currency:
        prose += f"<p>{CURRENCY_TEXT}</p>"
    body = (
        header
        + "<div>PART II</div><div>Item 8. Financial Statements and Supplementary Data</div>"
        + "\n".join(tables)
        + f"<div>NOTE 1 - ACCOUNTING POLICIES</div>{prose}"
    )
    return f"<!DOCTYPE html><html{html_attrs}><head><title>x</title></head><body>{body}</body></html>".encode()


def standard_doc(**kw) -> bytes:
    return make_doc(income(), comprehensive(), cashflow(), **kw)
