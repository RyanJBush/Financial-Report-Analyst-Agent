# Provenance of the evaluated filing

| Item | Value |
|---|---|
| Company / form | Microsoft Corporation (CIK 0000789019), Form 10-K for the fiscal year ended 2024-06-30 |
| Accession | 0000950170-24-087843 (filed 2024-07-30) |
| Document URL (claimed) | https://www.sec.gov/Archives/edgar/data/789019/000095017024087843/msft-20240630.htm |
| Reviewed snapshot | `msft-20240630.htm`, 6,861,379 bytes, SHA-256 `b0f255f67b2c26da21c69e05021ea333f4d42bafd415b153e8835a3ced2967fa` |
| Fact table | `examples/msft_fy2024/output/fact_table.json`, SHA-256 `9732bc41044ce39cc3c9f8405a4206354eb262dad799d59efeac79db946c7069` |

**How the bytes were obtained.** The HTML was supplied by the project owner (downloaded outside this project; the method and time were
not observed by the tool). It is recorded as an *offline import* with no invented HTTP fields (`tests/real/msft_fy2024/source_manifest_imported.json`).
The tool cannot prove the file is the genuine SEC document; the recorded SHA-256 identifies exactly the bytes that were reviewed.

**Size note.** The SEC filing index lists 6,860,911 bytes for this document; the reviewed snapshot has 6,861,379 bytes (468 more). The
difference is attributed to four script/noscript elements, but equivalence is **not proven**. Do not assume a later SEC download has
identical bytes; if the hash differs, re-run extraction and re-review rather than reusing these records.

**Not committed.** The filing bytes are not in this repository. To reproduce full (not snapshot) verification, obtain the file
yourself and use `finreport import-file` (README, "Ingest").

**Amendments.** No search for a later 10-K/A has been performed (no access to SEC from the build environment). Every answer says so.

**Currency policy.** USD only. A fact needs USD evidence that governs the financial statements (statement header/scale line, a
governing document sentence, or the amount's own inline-XBRL unit resolving to `iso4217:USD`). A dollar sign, debt denomination,
functional-currency or FX commentary is not evidence. Conflicting evidence blocks the fact.

**Calculation policy.** `Decimal`; absolute change = current - prior; percentage = (current - prior) / prior x 100 at 28 significant
digits, displayed to two decimals with ROUND_HALF_UP, and only when the prior amount is positive (otherwise the absolute change is
returned with an explicit reason). Facts are compared only if metric, scope, accounting basis, currency, and source filing match and
they are distinct annual periods of comparable length.


## Access and reuse guidance rechecked November 13, 2026

The [SEC access guide](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data)
states a maximum of 10 requests per second, asks callers to declare their User-Agent,
and asks that scripts download only what is needed. This tool caches files, limits
requests to one per second and requires the operator to provide a real contact string.
No contact address is invented or committed.

The [SEC reuse FAQ](https://www.sec.gov/about/webmaster-frequently-asked-questions)
states that EDGAR public filing content is free to access and reuse. This repository
contains facts, excerpts and hashes; the software license does not claim ownership
of Microsoft's filing. This guidance recheck is separate from source acquisition
and does not resolve the original download provenance or amendment search.
