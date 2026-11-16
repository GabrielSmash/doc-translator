# ADR 0004: A paragraph is translated whole, even when cut

- **Status:** Accepted
- **Date:** 2026-11-16
- **Cause:** T3 of [the fidelity sweep](../architecture.md#phase-8-translation-fidelity-by-cause)

## Context

Translated on its own, a piece of a paragraph is half a sentence. The model
guesses the rest, repeats it or invents it. Two things cut paragraphs:

- **A page break, or furniture in between:** a running header, a page
  number or a figure. "cable into the RJ-45 receptacle..." began page 19,
  its subject on page 18.
- **A blank span set in another size:** a space in 13 pt in an 11 pt line
  read as a change of font, so a new block began. This cut the page 19
  paragraph and the TOC entry "SELECTING WHETHER LOCAL FAULTS WILL BE
  MONITORED BY THE GLOBAL / FRAME STATUS".

A third, related failure: in a batch, the LLM sometimes answers one item
with the translation of its neighbor. "The two criteria are:" came back as
the text of the next list item.

The sweep counted 4 sentences restarted mid-way.

## Decision

1. **Line size and baseline ignore blank spans** (`Line.visible_spans`).
2. **Pieces of one paragraph are chained** (`analysis/continued.py`,
   `continued`). A piece goes on from the last one when:
   - it starts in lowercase;
   - the last one has no sentence end;
   - neither is a formula;
   - only furniture lies between them (blocks of up to 4 words);
   - both are set in the same font and size.
3. **A chain is translated as one text**, then shared back between its
   pieces in proportion to their source length, cut at spaces (`share`).
   A cut never passes its proportional point: an earlier piece ends a page,
   often with no room to spare.
4. **An answer that belongs to another item is retried** (`llm._shifted`).
   The answer must be closer to another item's source than to its own, by a
   margin of 0.2, with a similarity of at least 0.8.
5. Check: `restarted_sentences` in `tools/defects.py`.

## Consequences

- Manual, LLM engine: `restarted_sentences` 4 → 0. The page 19 paragraph
  is whole, the TOC entry is one item, and "Os dois critérios são:" is
  correct.
- The proportional cut may end a page piece on a different word than the
  source. The words are the translation's, so the reader sees no seam.
- NIST with pseudo-translation kept its full-size share (66.3%). A cut that
  went past the proportional point had dropped it to 60.5%.

## Open points

1. **Pieces in different styles** (a paragraph that changes font across the
   break) are not chained. No case was seen; rule 2 is conservative on
   purpose.
