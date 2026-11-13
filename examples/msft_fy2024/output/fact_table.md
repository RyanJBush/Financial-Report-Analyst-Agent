# Fact table: Microsoft Corporation 10-K, FYE 2024-06-30

- Accession: `0000950170-24-087843`
- Source: https://www.sec.gov/Archives/edgar/data/789019/000095017024087843/msft-20240630.htm
- Source SHA-256: `b0f255f67b2c26da21c69e05021ea333f4d42bafd415b153e8835a3ced2967fa`
- Facts: 6; conflicts: 0; issues: 0; exclusions: 7

| Evidence ID | Metric | Label | Raw | Reported (signed) | Scale | Normalized USD | Currency evidence | Period | Scope | Basis | Statement | Page | Row / Column | table:row:cell | Element ID |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| REV_2024 | revenue | Total revenue | 245,122 | 245122 | 1000000 | 245122000000 | ixbrl_unit_usd [U_USD=iso4217:USD] | FY2024 (2023-07-01 to 2024-06-30) | consolidated | US GAAP | INCOME STATEMENTS | 56 | Total revenue / 2024 | 24:11:3 | F_3c083f9d-9cbe-4037-b6c1-310e199227a1 |
| REV_2023 | revenue | Total revenue | 211,915 | 211915 | 1000000 | 211915000000 | ixbrl_unit_usd [U_USD=iso4217:USD] | FY2023 (2022-07-01 to 2023-06-30) | consolidated | US GAAP | INCOME STATEMENTS | 56 | Total revenue / 2023 | 24:11:7 | F_f6620662-6db2-4b98-bd6a-3b5658226fae |
| NI_2024 | net_income | Net income | $88,136 | 88136 | 1000000 | 88136000000 | ixbrl_unit_usd [U_USD=iso4217:USD] | FY2024 (2023-07-01 to 2024-06-30) | consolidated | US GAAP | INCOME STATEMENTS | 56 | Net income / 2024 | 24:36:3 | F_3c4d165a-f26d-4aa0-a83e-99380112214a |
| NI_2023 | net_income | Net income | $72,361 | 72361 | 1000000 | 72361000000 | ixbrl_unit_usd [U_USD=iso4217:USD] | FY2023 (2022-07-01 to 2023-06-30) | consolidated | US GAAP | INCOME STATEMENTS | 56 | Net income / 2023 | 24:36:7 | F_84f1f26e-ba72-4a86-b862-a7ee4578a2a2 |
| OCF_2024 | operating_cash_flow | Net cash from operations | 118,548 | 118548 | 1000000 | 118548000000 | ixbrl_unit_usd [U_USD=iso4217:USD] | FY2024 (2023-07-01 to 2024-06-30) | consolidated | US GAAP | CASH FLOWS STATEMENTS | 59 | Net cash from operations / 2024 | 27:25:3 | F_5e3b81df-e81c-454e-a9d2-a11ba652f66b |
| OCF_2023 | operating_cash_flow | Net cash from operations | 87,582 | 87582 | 1000000 | 87582000000 | ixbrl_unit_usd [U_USD=iso4217:USD] | FY2023 (2022-07-01 to 2023-06-30) | consolidated | US GAAP | CASH FLOWS STATEMENTS | 59 | Net cash from operations / 2023 | 27:25:7 | F_9a635dd1-2fdc-4aad-9c44-28c6703b8593 |

## Corroborating occurrences (matching duplicates)

- NI_2024: COMPREHENSIVE INCOME STATEMENTS / Net income = $88,136 (table 25, row 6)
- NI_2024: CASH FLOWS STATEMENTS / Net income = $88,136 (table 27, row 7)
- NI_2023: COMPREHENSIVE INCOME STATEMENTS / Net income = $72,361 (table 25, row 6)
- NI_2023: CASH FLOWS STATEMENTS / Net income = $72,361 (table 27, row 7)

## Conflicts

- none

## Issues

- none

## Exclusions (deliberately not used)

- statement_not_identified: revenue row 'Revenue' (table 13, row 4)
- statement_not_identified: net_income row 'Net income' (table 13, row 7)
- basis_non_gaap_differs_from_gaap: net_income row 'Net income' (table 19, row 18)
- statement_kind_not_applicable:equity: net_income row 'Net income' (table 28, row 19)
- statement_not_identified: revenue row 'Revenue' (table 49, row 6)
- basis_non_gaap_differs_from_gaap: revenue row 'Revenue' (table 50, row 6)
- basis_non_gaap_differs_from_gaap: net_income row 'Net income' (table 50, row 7)
