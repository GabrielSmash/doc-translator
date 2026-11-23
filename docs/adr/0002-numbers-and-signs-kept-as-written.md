# ADR 0002: Numbers and value signs are kept as written

- **Status:** Accepted
- **Date:** 2026-11-16
- **Cause:** T1 of [the fidelity sweep](../architecture.md#phase-8-translation-fidelity-by-cause)

## Context

A review of every translated unit of the equipment manual (LLM engine)
found 256 problems in 219 units. They were grouped by cause, and the causes
taken in order of harm: what changes a technical fact first.

In a technical document a number, a unit sign or a code carries the fact. A
wrong word is a style problem; a wrong number is a wrong instruction. The
sweep found:

| Source | Translation |
|---|---|
| `192.168.8.140` | `190.168.8.140` |
| `255.255.255.0` | `25-255.255.255.0` |
| `100Ω` | `100<tool_call>` |
| `258B` | `25-8B` |

Numbers were checked only when lost, and a problem was only logged. Added
numbers, lost signs and leaked control tokens of the chat model were not
seen at all.

## Decision

1. **One check for every engine** (`translators/checks.py`,
   `token_problems`):
   - numbers lost or added, compared by their digits, so `2.5`/`2,5` and
     `1,000`/`1.000` are the same; `½` counts as `1/2`;
   - codes changed: words that mix letters and digits, maybe joined by
     `_ - .` (`RJ45`, `CR2032`, `fc_release-2-19-b1`, `802.3u`). Spacing
     and hyphens may change (`3V` → `3 V`, `RJ45` → `RJ-45`), letters may
     not (`RJ45` → `RG45` is caught). Not codes: ordinals (`1st`), a common
     word typed against a number (`up to10`), and words joined by `/`, which
     in formulas and alternatives join ordinary words;
   - value signs lost (`Ω ® ™ © ° µ ± × ≤ ≥ ≈ ∞`). The OHM SIGN (U+2126)
     and the MICRO SIGN (U+00B5) are the same signs as the Greek letters a
     translation writes (U+03A9, U+03BC);
   - control tokens of the chat model.
2. **The LLM engine retries** an item with such a problem (severity 2 in
   `_check`). The retry lists the item's codes and numbers in the system
   message.
3. **A last try names what is still missing** (`missing_tokens`): "a
   previous translation dropped or changed these: RJ45". Retried without
   knowing what was wrong, the model dropped the same code again. The
   lesser problem of the three answers is kept.
4. **The fast engine protects codes like numbers** on its retry: it copies
   placeholders, so the sentence is translated again with every number and
   code replaced by one.
5. **What is still wrong is repaired only when unambiguous**
   (`repair_tokens`), the same way for every engine:
   - model tokens are removed;
   - a number split or stuttered with a hyphen is joined back, unless both
     sides are numbers of the source (`10-20`);
   - a number of the translation is replaced only when the source doesn't
     have it **and** the source's number in its place is missing from the
     translation; a reordered sentence is left alone;
   - a lost sign is written again after the same occurrence of its number
     (the third "100" of the source is the third of the translation), in
     place of letters the engine glued there ("100ósito").
6. **What is left is logged** and counted by the `changed_tokens` check of
   `tools/defects.py`.

## Consequences

- Manual, LLM engine: `changed_tokens` 6 → 2 with rules 1, 2, 5 and 6;
  → 0 with rules 3 and 4. The 2 were "RJ45" dropped from the Table 1
  caption and its TOC entry; both now keep it.
- Manual, fast engine: `changed_tokens` 0.
- No change on the corpus (arXiv, IRS, NIST) with the identity, pseudo and
  fast engines. The pseudo engine keeps codes as written, like the real
  engines.
- The last try costs one more call, only for the items still missing a
  number or a code after the retry.

## Refinements

- **2026-11-16:** omitted numbers were only reported; a last try now names
  them (rule 3). Codes were protected only by their digits; they are now
  checked whole (rules 1 and 4). Decided by the user.
- **2026-11-17:** found by the translation review: "100Ω" became
  "100ósito" unnoticed. The manual writes the OHM SIGN, U+2126, and the
  check knew only the Greek letter. Both count now, and the repair puts
  the sign back on the right "100".
- **2026-11-23:** found on the ISMUX-004 datasheet:
  - a value with its units ("1 kΩ", "43~63 Hz") is no longer sent to the
    engines (`base.has_words`): the LLM wrote "1 k" and "1 kcorlib".
    Up to two capital words are units or abbreviations ("3V3 LVTTL");
    more are a heading, translated (the first version left "UPGRADING
    MODULE FIRMWARE THROUGH THE 7700FC FRAME CONTROLLER" in English);
  - abbreviations are checked like codes (`checks.lost_abbreviations`):
    "BNC Female" became "Conector fêmea", "FEC" became "CORREÇÃO DE
    ERRO". An abbreviation is a capital word of 3 letters or more that is
    not a word of the source language ("NOTE", "STANDARD" are words); one
    the target writes its own way ("USA" -> "EUA") counts in that form.
    Retried at severity 2 and named in the last try, as codes;
  - pieces of one paragraph are checked together (`Result.groups`).
