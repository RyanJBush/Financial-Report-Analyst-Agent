# How `acceptance_checks.py` was reconstructed

The review was delivered as pasted text in which the script's line breaks and indentation were lost, and the text ended partway through
the third probe (`evaluation = evaluate(...)` for the debt-denomination input was the last statement; nothing assigned or wrote the results).
The reviewer stated the original file's SHA-256 as `8e36343b28213a2e5ec228016ccdf5a93df01ab48f22b02a7c60db22d75396a3`.

- **Restored:** line structure and indentation, inferred from the statements (context managers, `with` blocks, imports).
- **Completed (marked in the file):** the result entry for the third probe (mirroring the second), writing the JSON to `argv[2]`, and the
  print loop. No probe input, import, or assertion was altered.
- **Consequence:** the file's hash cannot match the reviewer's. `sha256sum` of the reconstruction is recorded in `RECONSTRUCTION.sha256`.
  The comparison that matters is behavioural: on the unchanged rev2 tree the reconstruction reproduced every row of the reviewer's
  observed table (control 0/0; changed cache: extract 5, evaluate 0, accepted; deleted cache: evaluate 0, accepted; both currency
  probes: six facts, kind `explicit_usd_statement`, accepted). See `review/c1c2/before/`.
- **Please re-run the reviewer's original file** against this packet if you still have it; it should behave as the `after/` results show.
