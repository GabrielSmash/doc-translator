# ADR 0005: Short texts left in English are checked

- **Status:** Proposed
- **Date:** 2026-11-16
- **Cause:** T4 of [the fidelity sweep](../architecture.md#phase-8-translation-fidelity-by-cause)

## Context

The sweep found 43 texts left untranslated and 15 translated in part. Most
are short:

| Where | Examples |
|---|---|
| Table cells (30) | Table 3, p14: `Pin`, `Key`, `Blue`, `Green`, `9 pin D Female`, `Ribbon Cable`, `PC End`; revision table, p3: `REVISION`, `DATE`, `Aug 01` |
| Spec labels, p11 | `Voltage:`, `Power:`, `Format:`, `Baud Rate:`, `7700FR-C frame mounting:` |
| Diagram labels | `Activity`, `SPEED` (while `Fault` and `ACTIVITY` were translated) |
| Leading word kept | `The 7700FC controlador...`, `Press W se você...`, `To atualizar...` |
| Whole sentence | `Complies with FCC Part 15 Class A and EU EMC directive` came back as `Comply with...` |

A run with logging showed that no batch failed: the model returned these
texts in English. Nothing checks for this, because "left as written" is a
valid answer for codes, names and kept terms. So it is never retried.

## Proposal

1. **A check, `untranslated`:** the answer equals the source, or keeps a
   run of source-language words. Kept terms, screen names, codes and
   numbers are excluded first. Source-language words are told by the
   language pair's stopwords plus a short-word dictionary.
2. **A partial check:** the answer starts with a source-language function
   word (`The`, `To`, `Press`) followed by target-language text.
3. **Retry** what the checks find, with the reason in the prompt ("this was
   left in English").
4. **Count it** in `tools/defects.py`, so the fast engine is measured too.

## To decide

1. **What may stay in English:** single words that are also Portuguese
   technical usage (`Status`, `Link`, `Setup`, `Baud`). A list per
   language pair, or the glossary's decision (ADR 0006).
2. **Month abbreviations in dates** (`Aug 01` → `Ago 01`): translate, or
   keep dates as written like numbers.
3. **Table 3 as a whole:** its context prompt already holds the table, so
   the model chose English for the whole table. The retry may return
   English again. A second step would send each untranslated cell alone.
