# Demo script (about 90 seconds)

Transcript of the same commands: [`demo_transcript.txt`](demo_transcript.txt).

1. **Frame it (10 s).** "One filing, three questions, and the program is only allowed to say what the evidence supports."
2. `finreport demo` - first question: point at the answer line, then **Facts used**: evidence ID `REV_2024`, the statement name and
   printed page, table/row/cell locator, the row text `Total revenue | 245,122 | 211,915 | 198,270`, and the period/scale/scope
   excerpts. Note the line `Verification: WEAKER ... reviewed snapshot` (clean clone has no filing bytes) and the
   `SCRIPTED TEMPLATE - not a model` label.
3. **Second question (20 s).** Show the calculation steps: `245122000000 - 211915000000 = 33207000000`, the unrounded percentage
   `15.66996...`, displayed `15.67%` with ROUND_HALF_UP. Both facts are cited.
4. **Third question (15 s).** Status `unresolved (forecast_not_supported)`: no value, no citation, no calculation, narrative "none generated".
5. **Live model (20 s, optional, needs Claude Code installed).**
   `finreport live "How much did consolidated revenue change from FY2023 to FY2024?"` - the narrative is labelled
   `LIVE MODEL claude-sonnet-5-5 via claude-cli; validated by deterministic checks`. Or show a file from `docs/runs/`.
6. **Evaluation (15 s).** `finreport eval-answers` - five dimensions, each with its own denominator; say that the cases are
   self-authored and frozen, not independent.
7. **Close (10 s).** "Scope is one filing. The amendment search and a live API run are still pending, and the README says so."
