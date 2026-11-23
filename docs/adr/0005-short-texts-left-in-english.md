# ADR 0005: Short texts left in English are checked

- **Status:** Accepted
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

## Decision

1. **A short text returned exactly as written is checked**
   (`checks.unchanged_words`). Its ordinary words count as left in
   English. Not counted:
   - kept terms (labels, screen names, quoted names, "N/A");
   - codes and short all-capital abbreviations ("GND", "LED");
   - dates ("Aug 01", rule 3);
   - texts with a formula ("PE(pos,2i) = sin(...)").
2. **What the document tells decides what is a word** (`Vocabulary`):
   - only words the document also writes in lowercase in its running text
     count. Words it only writes capitalized are names ("Vaswani",
     "Google Brain", "Ethernet");
   - a text that is, whole, a name its screens show or it keeps ("Gateway",
     "login:") is that name.
3. **Month abbreviations of dates are written by a table**
   (`LanguagePair.localize_dates`), not by the engines: "Aug 01" → "Ago 01",
   "SEP 04" → "SET 04". Only abbreviations the source has, before a day or
   a year. Not for the measuring engines (identity, pseudo).
4. **A sentence whose first word was left in English** before a translated
   rest is checked (`checks.leading_source_word`): "The 7700FC
   controlador...", "Press W se você...", "To atualizar...". Only words that
   start English sentences and are not Portuguese words
   (`LanguagePair.source_only_starts`): "Use" or "Note" start sentences in
   both languages.
5. **The LLM engine retries** what rules 1 and 4 find (severity 3, like a
   sentence left untranslated). If the retry returns the text as written
   again, it is kept: the model judged it a name.
6. Check: `left_in_english` in `tools/defects.py`, the same rules.

## Consequences

- Manual, LLM engine: short texts returned as written with ordinary words
  88 → 61 (the 61 left are names, codes and abbreviations: "IP Address",
  "Tx", "Gnd"); `left_in_english` reports 4 ("SERIAL I/O", "SFF POWER",
  "EVERTZ"). Sentences starting with an English word: 5 → 0.
- Translated now: the revision table ("REVISÃO", "DESCRIÇÃO", "Ago 01",
  "Dez 04"), the spec labels ("Taxa de Baud:", "Alimentação:", "Montagem
  em quadro 7700FR-C:"), Table 3 ("Pino", "Chave", "Fêmea D de 9 pinos").
- Single words retried without context bring wrong words: "PC End" →
  "Fim do PC" (the cable's end, "extremidade"), "Ribbon Cable" → "Cabo de
  faixa", "2 row X 3 pin Berg" → "2 filete X 3 pino Berg", "Baud" →
  "Velocidade"; a test run also gave "Pin" → "Pinho". These are domain
  terms, the subject of ADR 0006; the user chose to accept them until
  then.
- The identity engine counts every text in `left_in_english`: it returns
  the source. The column only means something for translating engines.

## Revision: an English dictionary decides what is a word (2026-11-23)

Rule 2 missed words a document writes only capitalized, in tables. Table 3
of the manual came back with "Blue", "Yellow", "Orange" in English and
"Verde", "Vermelho", "Marrom" translated: the model returned the whole
table as written (it does so in some runs, with or without the ADR 0006
context), and only the colors the running text also writes in lowercase
("green LED") were retried.

- A word the source language's dictionary has in lowercase is a word:
  SCOWL's en_US Hunspell dictionary (LibreOffice's repository, same pinned
  commit as VERO; permissive license), downloaded on first use like the
  pt-BR one (`LanguagePair.source_dictionary`).
- A short text with a word that is neither is a name, whole ("Google
  Brain", "EVERTZ (2) 7700FC"): "Brain" is a word, but not there.
- Words the target language has too ("flash", "status", "software",
  "link") are not left in English: "64 MB Flash" kept is not retried.
  A false friend kept alone ("Data" for "Dados") is missed.
- Manual, page 14: the six colors, "6 Condutores" and "Cabo de fita"
  translated.
- arXiv, fast engine: `left_in_english` 9 → 10, other texts. Found now:
  "Abstract", "Parser" (real). Gone: "Deep-Att + PosUnk", "Luong et al.",
  "preprint arXiv:..." (false alarms). New false alarms: "Google Brain"
  ("google" is an English verb) and "Inc., 2015.". A false alarm costs
  the LLM engine a retry; a retry returned as written is kept (rule 5).

## Decisions taken

- **2026-11-17:** the three points were decided by the user: the check now
  (not with ADR 0006), months by a table, and the first-word check.
- **2026-11-23:** the English dictionary, chosen by the user over retrying a
  batch returned unchanged; words of both languages not counted.
