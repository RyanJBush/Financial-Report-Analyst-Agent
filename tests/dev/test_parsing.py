from decimal import Decimal

import pytest

from finreport.parsing import AmountError, is_dash, looks_like_amount, parse_amount, parse_document


@pytest.mark.parametrize("raw,expected", [
    ("1,234", Decimal(1234)), ("$ 1,234", Decimal(1234)), ("(1,234)", Decimal(-1234)), ("$(1,234)", Decimal(-1234)),
    ("-1,234", Decimal(-1234)), ("−1,234", Decimal(-1234)), ("12.5", Decimal("12.5")), ("0", Decimal(0)),
])
def test_parse_amount_accepts(raw, expected):
    assert parse_amount(raw) == expected


@pytest.mark.parametrize("raw", ["(1,234", "1,234)", "--5", "(-5)", "12%", "abc", "1,23,4", "", "—"])
def test_parse_amount_rejects(raw):
    with pytest.raises(AmountError):
        parse_amount(raw)


def test_amount_and_dash_classification():
    assert looks_like_amount("$1,234") and looks_like_amount("(5)") and not looks_like_amount("16%")
    assert not looks_like_amount("Total revenue") and is_dash("—") and is_dash("$—") and not is_dash("0")


def test_split_cells_merge_into_logical_values():
    html = ("<table><tr><td>Net loss</td><td>$</td><td>(1,234</td><td>)</td><td></td><td>5</td><td>%</td></tr></table>")
    row = parse_document(html).tables[0].rows[0]
    assert [c.text for c in row.logical] == ["Net loss", "$(1,234)", "5%"]


def test_physical_cell_index_survives_merging():
    html = "<table><tr><td>Label</td><td></td><td>$</td><td>7</td></tr></table>"
    cell = parse_document(html).tables[0].rows[0].logical[1]
    assert (cell.text, cell.cell_index) == ("$7", 3)


def test_section_and_printed_page_tracking():
    html = ("<div>PART I</div><div>Item 1. Business</div><table><tr><td>a</td></tr></table><div>12</div>"
            '<hr style="page-break-after:always"/>'
            "<div>PART II</div><div>Item 8. Financial Statements</div><table><tr><td>b</td></tr></table><div>56</div>"
            '<hr style="page-break-after:always"/><table><tr><td>c</td></tr></table>')
    t = parse_document(html).tables
    assert (t[0].section, t[0].printed_page) == ("Part I, Item 1", 12)
    assert (t[1].section, t[1].printed_page) == ("Part II, Item 8", 56)
    assert t[2].printed_page is None  # no footer number on the last page


def test_hidden_inline_xbrl_header_and_comments_are_not_text():
    html = ("<ix:header><div>hidden-ix</div></ix:header><div style='display:none'>hidden-div</div>"
            "<!-- comment --><div>visible</div>")
    assert parse_document(html).blocks == ["visible"]


def test_ix_attributes_and_ids_are_captured():
    html = '<table id="t1"><tr id="r1"><td id="c1">x</td><td><ix:nonFraction id="n1" scale="6">5</ix:nonFraction></td></tr></table>'
    table = parse_document(html).tables[0]
    row = table.rows[0]
    assert (table.element_id, row.element_id, row.cells[0].element_id) == ("t1", "r1", "c1")
    assert row.logical[1].element_id == "n1" and row.logical[1].ix["scale"] == "6"
