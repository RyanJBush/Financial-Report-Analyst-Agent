"""HTML -> tables + text blocks, with the context the extractor needs.

Index conventions (also documented on schemas.Locator) are stable for a given file
checksum because they depend only on document order:
  table_index = position among top-level <table> elements
  row_index   = position among <tr> inside that table
  cell_index  = position among direct <td>/<th> children of that row
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from decimal import Decimal

from bs4 import BeautifulSoup, NavigableString, Tag
from bs4.element import PreformattedString

_WS = re.compile(r"\s+")
BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "body", "br", "caption", "center", "dd", "div", "dl",
    "dt", "fieldset", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr",
    "html", "li", "main", "nav", "ol", "p", "pre", "section", "table", "tr", "ul",
}
SKIP_TAGS = {"script", "style", "head", "title", "ix:header"}
PREFIX_TOKENS = {"$", "(", "$(", "($"}
PAGE_NUMBER_RE = re.compile(r"^[-–\s]*(?:page\s+)?(\d{1,3})[-–\s]*$", re.I)
PART_RE = re.compile(r"^PART\s+(IV|I{1,3})\b", re.I)
ITEM_RE = re.compile(r"^ITEM\s+(\d{1,2}[A-C]?)\b", re.I)
_AMOUNT_RE = re.compile(r"^\$?\s*\(?\s*\$?\s*[-−]?\d[\d,]*(?:\.\d+)?\s*\)?$")
_NUMBER_BODY_RE = re.compile(r"^(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$")
DASHES = {"—", "–", "-", "−", "$—", "$-", "$–"}


_HIDDEN_STYLE_RE = re.compile(
    r"display:none|visibility:(?:hidden|collapse)|mso-hide:all|opacity:0(?:\.0+)?(?:;|$)|font-size:0(?:px|pt|em|rem|%)?(?:;|$)"
)
# Colour-only concealment (white/transparent text) cannot be judged without knowing the background.
_AMBIGUOUS_STYLE_RE = re.compile(r"(?:^|;)color:(?:white|#fff|#ffffff|transparent)(?:;|$)")


def _style(tag: Tag) -> str:
    return (tag.get("style") or "").replace(" ", "").lower()


def is_hidden(tag: Tag) -> bool:
    """True for elements that are certainly not rendered: display/visibility/size/opacity styles, the HTML
    `hidden` attribute, and inline-XBRL <ix:hidden>. One predicate for text inside and outside tables."""
    if tag.name in ("ix:hidden",) or tag.has_attr("hidden"):
        return True
    return bool(_HIDDEN_STYLE_RE.search(_style(tag)))


def _hidden_between(tag: Tag, stop: Tag) -> bool:
    node: Tag | None = tag
    while node is not None and node is not stop:
        if is_hidden(node):
            return True
        node = node.parent
    return False


def visible_text(node: Tag) -> tuple[str, bool, bool]:
    """(text, hidden_text_removed, ambiguous_visibility) from the rendered descendants of node."""
    parts: list[str] = []
    removed = ambiguous = False
    for child in node.children:
        if isinstance(child, PreformattedString):
            continue
        if isinstance(child, NavigableString):
            parts.append(str(child))
        elif isinstance(child, Tag):
            if child.name in SKIP_TAGS or is_hidden(child):
                removed = True
                continue
            if _AMBIGUOUS_STYLE_RE.search(_style(child)):
                ambiguous = True
            text, r, a = visible_text(child)
            parts.append(text)
            removed, ambiguous = removed or r, ambiguous or a
    return "".join(parts), removed, ambiguous


def clean(text: str) -> str:
    return _WS.sub(" ", text.replace("\xa0", " ").replace("​", "")).strip()


class AmountError(ValueError):
    pass


def is_dash(text: str) -> bool:
    return text.replace(" ", "") in DASHES


def looks_like_amount(text: str) -> bool:
    t = text.replace(" ", "")
    return bool(_AMOUNT_RE.match(t)) and t.count("(") == t.count(")")


def parse_amount(raw: str) -> Decimal:
    """Parse '$ (1,234)', '1,234', '-1,234' into a signed Decimal. Raises AmountError otherwise."""
    t = raw.replace(" ", "").replace("−", "-")
    t = t.lstrip("$")
    negative = False
    if t.startswith("(") and t.endswith(")"):
        negative, t = True, t[1:-1]
    elif t.startswith("(") or t.endswith(")"):
        raise AmountError(f"unbalanced parentheses in {raw!r}")
    t = t.lstrip("$")
    if t.startswith("-"):
        if negative:
            raise AmountError(f"double negative in {raw!r}")
        negative, t = True, t[1:]
    if not _NUMBER_BODY_RE.match(t):
        raise AmountError(f"not an amount: {raw!r}")
    value = Decimal(t.replace(",", ""))
    return -value if negative else value


@dataclass
class Cell:
    index: int
    text: str
    element_id: str | None
    ix: dict[str, str] | None
    hidden_text_removed: bool = False
    ambiguous_visibility: bool = False


@dataclass
class LogicalCell:
    """Visible value after merging Workiva-style split cells ('$' | '1,234' | ')')."""

    text: str
    cell_index: int  # physical cell holding the number/label
    element_id: str | None
    ix: dict[str, str] | None
    ambiguous_visibility: bool = False


@dataclass
class Row:
    index: int
    element_id: str | None
    cells: list[Cell]
    logical: list[LogicalCell]

    def text(self) -> str:
        return " | ".join(c.text for c in self.logical)


@dataclass
class Table:
    index: int
    element_id: str | None
    rows: list[Row]
    pre_context: list[str]
    part: str | None
    item: str | None
    printed_page: int | None = None

    @property
    def section(self) -> str | None:
        parts = []
        if self.part:
            parts.append(f"Part {self.part}")
        if self.item:
            parts.append(f"Item {self.item}")
        return ", ".join(parts) or None


@dataclass
class UnitDef:
    """An inline-XBRL unit definition found under <ix:resources>."""

    unit_id: str | None
    index: int  # position among xbrli:unit elements under ix:resources, document order (0-based)
    element_id: str | None
    measures: list[str]
    has_divide: bool


@dataclass
class Document:
    tables: list[Table] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)  # text blocks outside tables, document order
    units: list[UnitDef] = field(default_factory=list)
    namespaces: dict[str, set[str]] = field(default_factory=dict)  # xmlns:prefix declarations seen on html / ix / xbrli elements


def merge_cells(cells: list[Cell]) -> list[LogicalCell]:
    out: list[LogicalCell] = []
    prefix = ""
    for c in cells:
        t = c.text
        if not t:
            continue
        if t in PREFIX_TOKENS:
            prefix += t
            continue
        if out and set(t) <= set(")%"):
            out[-1] = replace(out[-1], text=out[-1].text + t)
            continue
        ix_id = (c.ix or {}).get("id")
        out.append(LogicalCell(text=prefix + t, cell_index=c.index, element_id=ix_id or c.element_id, ix=c.ix,
                               ambiguous_visibility=c.ambiguous_visibility))
        prefix = ""
    return out


def _first_visible_ix(td: Tag) -> Tag | None:
    for tag in td.find_all("ix:nonfraction"):
        if not _hidden_between(tag, td) and not is_hidden(td):
            return tag
    return None


def _build_row(tr: Tag, index: int, table: Tag) -> Row:
    """Indices count every <tr>/<td> in document order, hidden or not, so locators stay stable.
    Hidden rows/cells keep their position but contribute no text."""
    cells: list[Cell] = []
    row_hidden = _hidden_between(tr, table)
    for c_idx, td in enumerate(tr.find_all(["td", "th"], recursive=False)):
        if row_hidden or is_hidden(td):
            cells.append(Cell(index=c_idx, text="", element_id=td.get("id"), ix=None, hidden_text_removed=True))
            continue
        text, removed, ambiguous = visible_text(td)
        ix_tag = _first_visible_ix(td)
        ix = {k: str(v) for k, v in ix_tag.attrs.items()} if ix_tag else None
        cells.append(Cell(index=c_idx, text=clean(text), element_id=td.get("id"), ix=ix,
                          hidden_text_removed=removed, ambiguous_visibility=ambiguous))
    return Row(index=index, element_id=tr.get("id"), cells=cells, logical=merge_cells(cells))


class _Walker:
    def __init__(self) -> None:
        self.doc = Document()
        self.buf: list[str] = []
        self.since_table: list[str] = []
        self.page_tables: list[Table] = []
        self.page_last: str | None = None
        self.part: str | None = None
        self.item: str | None = None

    def flush(self) -> None:
        text = clean("".join(self.buf))
        self.buf = []
        if not text:
            return
        self.doc.blocks.append(text)
        self.since_table.append(text)
        self.page_last = text
        if len(text) <= 120:
            if m := PART_RE.match(text):
                self.part = m.group(1).upper()
            if m := ITEM_RE.match(text):
                self.item = m.group(1).upper()

    def page_break(self) -> None:
        self.flush()
        if self.page_last and (m := PAGE_NUMBER_RE.match(self.page_last)):
            for t in self.page_tables:
                t.printed_page = int(m.group(1))
        self.page_tables = []
        self.page_last = None

    def add_table(self, tag: Tag) -> None:
        table = Table(
            index=len(self.doc.tables),
            element_id=tag.get("id"),
            rows=[_build_row(tr, i, tag) for i, tr in enumerate(tag.find_all("tr"))],
            pre_context=self.since_table[-8:],
            part=self.part,
            item=self.item,
        )
        self.since_table = []
        self.doc.tables.append(table)
        self.page_tables.append(table)

    def walk(self, node: Tag) -> None:
        for child in node.children:
            if isinstance(child, PreformattedString):
                continue  # comments, doctype, CDATA
            if isinstance(child, NavigableString):
                self.buf.append(str(child))
                continue
            if not isinstance(child, Tag):
                continue
            if child.name in SKIP_TAGS or is_hidden(child):
                continue
            style = _style(child)
            if "page-break-before:always" in style or "break-before:page" in style:
                self.page_break()
            if child.name == "table":
                self.flush()
                self.add_table(child)
            else:
                block = child.name in BLOCK_TAGS
                if block:
                    self.flush()
                self.walk(child)
                if block:
                    self.flush()
            if "page-break-after:always" in style or "break-after:page" in style:
                self.page_break()


_NS_ELEMENTS = ("html", "ix:header", "ix:resources", "xbrli:unit", "xbrli:measure", "xbrli:divide")


def _collect_units(soup: BeautifulSoup, doc: Document) -> None:
    for tag in soup.find_all(_NS_ELEMENTS):
        for attr, value in tag.attrs.items():
            if attr.startswith("xmlns:"):
                doc.namespaces.setdefault(attr[len("xmlns:"):], set()).add(str(value))
    index = 0
    for resources in soup.find_all("ix:resources"):
        for unit in resources.find_all("xbrli:unit"):
            doc.units.append(UnitDef(
                unit_id=unit.get("id"), index=index, element_id=unit.get("id"),
                measures=[clean(m.get_text("")) for m in unit.find_all("xbrli:measure")],
                has_divide=unit.find("xbrli:divide") is not None))
            index += 1


def parse_document(html: bytes | str) -> Document:
    soup = BeautifulSoup(html, "html.parser")
    walker = _Walker()
    walker.walk(soup)
    walker.page_break()
    _collect_units(soup, walker.doc)
    return walker.doc


def resolve_locator(doc: Document, table_index: int, row_index: int, cell_index: int) -> str:
    """Return the physical cell text a locator points to (reproducibility check)."""
    row = doc.tables[table_index].rows[row_index]
    return row.cells[cell_index].text
