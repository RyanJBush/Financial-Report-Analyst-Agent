# Architecture and evidence flow

```
  SEC 10-K HTML (one filing)                    (never committed; 6.8 MB)
        |  fetch (declared User-Agent) | import-file (offline, hash-checked)
        v
  data/cache/<file>  +  data/source_manifest.json      <- filename, bytes, SHA-256 recorded; re-verified before every answer
        |  extract            (parsing.py, extract.py: statement tables, inline XBRL, scale, currency, period, scope)
        v
  output/fact_table.json                                <- six source-linked facts: value, scale, period, scope, basis, currency,
        |                                                  locator (table/row/cell/element), source checksum, excerpts
        |  load_factbook      (answers.py: re-verify source; or, with no filing bytes, the labelled snapshot mode)
        v
  FactBook  --answer_question-->  Answer (answered | unresolved | refused)
                                    value + evidence refs + calculation record (Decimal) | reason code, no value/citation
        |  build_bundle       (narrative.py: ONLY the cited facts + calculation, never the whole table or the filing text)
        v
  VerifiedBundle --adapter--> model JSON {"claims": [{text, evidence_ids, calculation_id}]}
        |                       scripted template | claude-cli | anthropic-api
        |  parse_model_output + validate_claims   (deterministic; no model)
        v
  NarrativeResult: validated | rejected | unavailable | not_generated      (text exists only when validated)
        |  display.render
        v
  CLI output: question, status, concise answer, facts, calculation, supporting excerpts, verification level, narrative, limitations
```

## Components

| Module | Role | Reads the frozen expectations? |
|---|---|---|
| `ingest.py`, `manifest.py` | cache-first polite fetch, offline import, source verification | no |
| `parsing.py`, `extract.py`, `schemas.py` | HTML to tables, fact extraction, strict models | no |
| `evaluation.py` | extraction evaluation: numeric diagnostic and separate machine acceptance | **only module that does** |
| `answers.py` | question grammar, fact book, comparability, Decimal calculation, answer validators | no |
| `narrative.py` | bundle, claim contract, validator, adapters, bounded run loop | no |
| `display.py` | text rendering | no |
| `answer_eval.py` | scores the frozen answer cases (five separate dimensions) | no (reads `answer_cases.json`; no production module reads it) |
| `cli.py` | commands | passes the path to `evaluation.py` only |

## Decisions that matter

* **The model never calculates and never sees the filing.** It restates a small verified bundle. Everything it returns is checked
  mechanically; text that fails is not shown. The structured answer is the product; the narrative is optional garnish.
* **Unresolved is a first-class result.** Forecasts, unextracted metrics/periods, other companies and unsupported question shapes
  return no value, no citation, no calculation, and never reach a model.
* **Two verification levels, always labelled.** `cached_source_verified` (the filing bytes were re-hashed and match the manifest) and
  `reviewed_snapshot_fact_table_only` (clean clone: the fact table matches the recorded hash of the reviewed snapshot; the bytes are
  not present). The weaker level is printed in every output. `ask` never uses the weaker level. A cached file that is present but
  wrong is never excused.
* **Claim contract.** Exactly the keys `text`, `evidence_ids`, `calculation_id`; 1-4 claims; bounded length. Validator rules and their
  limits are listed in the `narrative.py` docstring. The forecast/causal/framing filters are keyword lists, not language understanding;
  they are deliberately conservative (for example the word "because" is always rejected).
* **Bounded provider use.** At most 3 attempts (default 2), per-call timeout, output-token cap, one retry path only after a
  *validation* failure; a provider failure is never retried within a run and yields `unavailable`.
* **Credentials.** The project stores none. `claude-cli` uses the operator's own authenticated Claude Code installation;
  `anthropic-api` reads `ANTHROPIC_API_KEY` at call time, sends it only as a request header, and never logs or echoes it (including in
  error paths; tested).
