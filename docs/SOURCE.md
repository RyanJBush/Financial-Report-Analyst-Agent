# Source and provenance

Company: Microsoft Corporation. CIK: `0000789019`. Form: 10-K.
Accession: `0000950170-24-087843`. Fiscal year ended June 30, 2024.
Filing date: July 30, 2024.

- [Filing HTML](https://www.sec.gov/Archives/edgar/data/789019/000095017024087843/msft-20240630.htm)
- [SEC filing index](https://www.sec.gov/Archives/edgar/data/789019/000095017024087843/0000950170-24-087843-index.htm)
- [Recorded manifest](../data/source_manifest.json)

The file was supplied as an HTML attachment and imported offline. Neither the
external download time nor the external retrieval method was observed. The manifest
does not invent HTTP headers or a download timestamp. The received bytes are
compressed losslessly in `data/filing.html.gz` and restored only as parser input.

Received size: **6,861,379 bytes**. SHA-256:
`b0f255f67b2c26da21c69e05021ea333f4d42bafd415b153e8835a3ced2967fa`.
The SEC index lists **6,860,911 bytes**. The 468-byte difference equals the combined
script/noscript element lengths found in the attachment; this does not establish
equivalence to the SEC's indexed bytes. No script was executed or stripped.

## Access and reuse guidance checked November 13, 2026

The [SEC access guide](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data)
states a maximum of 10 requests per second, asks callers to declare a User-Agent,
and asks that automated retrieval minimize load. This tool uses the stricter one
request per second and caches results. Supply an actual contact string through
`SEC_USER_AGENT` if fetching; no contact identity is invented or committed.

The [SEC reuse FAQ](https://www.sec.gov/about/webmaster-frequently-asked-questions)
states that EDGAR public filing content is free to access and reuse. The software's
MIT license does not claim ownership of Microsoft's filing. The bundled HTML is
public filing content with explicitly recorded provenance limitations.

## Annual facts

All amounts below are positive consolidated US GAAP amounts, USD millions.

| Metric | Original label | FY2023 | FY2024 | Printed page |
|---|---|---:|---:|---:|
| Revenue | Total revenue | 211,915 | 245,122 | 56 |
| Net income | Net income | 72,361 | 88,136 | 56 |
| Operating cash flow | Net cash from operations | 87,582 | 118,548 | 59 |

FY2023 spans July 1, 2022–June 30, 2023; FY2024 spans July 1, 2023–June 30, 2024.
Each amount's own inline-XBRL unit resolves to `iso4217:USD`; context periods,
entity ID, absence of dimensions, concept and visible cells were checked directly.
This is an in-filing check, not a separate SEC companyfacts API cross-check.

The [reference fact table](../evidence/reference_fact_table.json) records exact cell
locators, raw labels and values, normalization, repeated occurrences and excerpts.
The [direct source audit](../evidence/independent_source_audit.json) does not import
production parsing/extraction functions. Its script has been adapted to repository
paths and can be rerun:

```bash
python evidence/independent_audit.py data/cache/msft-20240630.htm
```

Matching repeated net-income figures are corroboration. No unresolved conflicts
were found in this run. A limited web search for FY2024 amendments did not produce
a verified amendment record; it is not an exhaustive EDGAR search. The manifest
therefore retains amendment status `pending`. All answers refer to this selected
accession as presented, without claiming it is the latest amended presentation.
