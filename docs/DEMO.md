# Two-minute demo

1. Run `python scripts/demo.py`. Explain that the source is a checksum-pinned,
   user-supplied Microsoft FY2024 10-K. No network is needed for this demo.
2. Show `finreport ask "What was revenue in FY2024?"`: USD 245,122 million,
   evidence ID `REV_2024`, original label, period, scale and source excerpts.
3. Show `finreport ask "How did revenue change from FY2023 to FY2024?" --json`:
   USD 33,207 million, 15.67%, both evidence IDs and the exact Decimal calculation.
4. Show `finreport ask "What will revenue be in FY2027?"`: unresolved with no
   fabricated forecast or citation. Exit 1 is the expected outcome.
5. Open `evidence/evaluation_report.json` and `docs/EVALUATION.md`. Explain 6/6
   source facts and 12/12 bounded question cases, and the limits of those results.
6. State that the default adapter is scripted. The optional local-model adapter
   was protocol-tested with mocks; live-model behavior has not been established.

Stop after the three questions and the evidence report. Do not claim general
financial-analysis accuracy, a completed amendment search or a deployed web app.
