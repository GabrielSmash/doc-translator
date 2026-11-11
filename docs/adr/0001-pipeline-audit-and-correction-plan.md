# ADR 0001: Pipeline audit and correction plan

- **Status:** Accepted
- **Date:** 2026-11-11

## Context

Until this audit, layout rules were tuned by looking at a few pages (2, 5, 8,
16) of a single document, `samples/7700.pdf`. To check whether the pipeline
was on the right path, it was measured instead of inspected.

### How it was measured

- **Corpus of four documents:** the 29-page equipment manual, a two-column
  LaTeX paper (arXiv 1706.03762, 6 pages), a dense form (IRS W-4, 4 pages)
  and a Word-generated report (NIST SP 800-207, 8 pages).
- **Instrumentation** of every stage: time, share of the text extracted,
  font reduction, overlapping lines, links, drawings and images preserved,
  searchable text, and similarity of the page's ink profile to the original.
- **Identity run:** the original text, untranslated, sent through the render
  path. Whatever differs from the input is a renderer defect, not an effect
  of translation.

### What is working

| Measure | Result |
|---|---|
| Text extracted for translation | 99.0% to 99.9% on the corpus; 82% on the manual (5.3% is code, the rest TOC leaders and numbering) |
| Images preserved | 100% |
| Text placed outside the page, or not fitted | none |
| Searchable text in the output | 99% (89.9% on NIST, because of defect 3) |
| Fast engine, manual | 8.5 s translating, 9.4 s rendering |
| LLM engine, manual | 532 s translating (98% of the run); batches are 77% of that |

### Defects found

| # | Defect | Evidence |
|---|---|---|
| 1 | Insertion box too short in height | With identical text, 7% to 23% of paragraphs are reduced. On the manual, 133 of 143 cases are fixed by height alone: the box is 1.12x the font size and the text needs 1.2x (its line height) |
| 2 | Tables with a rule on every row are destroyed | Manual pages 17 and 29: rules taken as underlines, cells merged, 40 drawings removed, white-on-black header lost |
| 3 | Font coverage check accepts fonts that cannot draw accents | NIST: every "ç", "ã", "ó" came out blank. The glyph slot exists and has a width, but its outline has zero pixels |
| 4 | Fallback font family chosen by font name | IRS: 80% of the document became serif because "HelveticaNeueLTStd-Roman" contains "roman"; Computer Modern became sans-serif on arXiv |
| 5 | Internal links destroyed | NIST: 9 links in, 1 out (8 are internal GOTO links) |
| 6 | Rotated text handled as horizontal | NIST: 8 vertical margin lines rendered as loose horizontal text |
| 7 | Output four times larger | 789 KB to 3057 KB, from 675 copies of the same ToUnicode table. Saving with `garbage=4` gives 1222 KB with identical rendering |
| 8 | LLM step that locates bold and links | 94 s (18% of LLM time) for 26 exact hits out of 117. Styles carried over: 88.9%, against 100% in the fast engine |
| 9 | Fast engine does not check numbers | Numbers lost in 2.9% (arXiv) and 5.0% (IRS) of paragraphs; 0.4% on the manual |

Overlapping lines grow in every document and already appear in the identity
run (IRS: 4 to 192 pairs), so they are structural and tied to defect 1.

### A wrong assumption this corrects

Font reduction had been attributed to Portuguese being longer than English.
On the manual, 76.7% of paragraphs keep their full size with the original
text and 71.3% with the translation: translation accounts for about 5
points, and defect 1 for the rest.

## Decision

The work follows the phases of [the architecture document](../architecture.md),
which also covers the structural changes behind these defects.

1. **Bring the measurement into the repository first** as a regression
   harness, together with synthetic fixtures and a public corpus downloaded
   on demand. A layout change is accepted only after it is run on several
   document types and compared with the identity run.

2. **Fix the defects in this order:**

   | Phase | Defect | Approach |
   |---|---|---|
   | 1 | 3: font coverage | Decide coverage by rendering each needed character and checking for ink |
   | 1 | 2: bordered tables | Accept a stroke as an underline only when it matches the extent of the text; stop removing line art by touch |
   | 1 | 7: output size | Save with `garbage=4` |
   | 1 | 9: numbers in the fast engine | Apply the number check to every engine |
   | 4 | 1: box height | Stop using neighboring line boxes as hard limits; bound the text by baselines and real obstacles |
   | 4 | 4: fallback family | Read the serif and monospace bits from the span flags instead of the font name |
   | 4 | 5: internal links | Save links before redaction and restore the ones that were not recreated |
   | 4 | 6: rotated text | Leave lines whose direction is not horizontal untouched |
   | 5 | 8: LLM style location | Translate each styled phrase and match it in the paragraph, as the fast engine does |

   Defects 3 and 2 come first because they corrupt content and their fixes
   are local. Defects 7 and 9 go with them because they are a few lines each.

3. **Defect 1 waits for phase 4,** after placement is separated from
   drawing (phases 2 and 3 add the report, the translation cache and the
   new document model). It changes how every paragraph is placed, which is
   the highest regression risk in the plan, and by then it can be tested
   without producing a PDF.

### Found by the harness in phase 0

The measurement harness detects characters left without ink on the rendered
page, which the audit could not. It found two more problems, both caught by
the regression tests:

| # | Defect | Evidence |
|---|---|---|
| 3 (extended) | Ligature glyphs of reused subset fonts are empty: the HTML engine draws "ﬁ", "ﬀ", "ﬃ", "ﬂ" with them | NIST, untranslated text: 55 blank characters, all ligatures |
| 10 | White text on a dark fill becomes invisible when the translation wraps outside the fill | Manual pages 17 and 29: the table header "Parameters" falls below the black band, white on white |

Defect 10 joins phase 4, with the other placement work.

### Phase 1 results

| Defect | Status | Measured |
|---|---|---|
| 3: blank accented letters | Fixed | NIST with pseudo-translation: 8,941 blank characters to 0. Coverage is decided by drawing each character with the font alone and checking for ink |
| 3 (extended): blank ligatures | Fixed | NIST untranslated: 53 blank characters to 0, original font kept on 100% of the text (was 94%). fontTools, now a runtime dependency, removes the ligature codes from reused TrueType fonts, plus symbols they can't draw, which the CSS fallback font then draws. Output text is searchable as written ("office", not "oﬃce") |
| 2: bordered tables | Fixed | Manual pages 17 and 29: 108 and 294 drawings kept (all). A stroke is an underline only if it stays within its line; only drawings made of underlines alone are removed, and only when entirely covered |
| 4: fallback family | Fixed, moved from phase 4 | Fixing defect 3 sends more text to the fallback font, so a wrong family weighed more. Known family names decide first, then the PDF flags. arXiv with pseudo-translation: full size 63.6% to 72.7%, layout similarity 0.64 to 0.81 |
| 7: output size | Fixed | Manual 3.87x to 1.55x, arXiv 8.2x to 1.25x, NIST 2.8x to 1.08x |
| 9: numbers in the fast engine | Fixed | Numbers lost: 0% on the four documents (arXiv was 4.8%) |

Found while fixing:

- The serif fallback was Charis SIL, 10% wider than Times; it is now
  Nimbus Roman, metric-compatible with Times New Roman.
- Rules between table cells and the edges of filled areas now separate
  paragraphs: cells of the manual's tables were merged before. A stroke
  must span half of the line to count, or the bar of a square root (√dk)
  split arXiv paragraphs.
- IRS output stays 3.6x larger for another reason: `insert_htmlbox`
  creates one Form XObject per paragraph (681 tiny ones). To address in the
  draw stage, phase 4.
- Rows of tight tables shrink to 93%: that is defect 1, phase 4. With table
  cells now separate paragraphs, the manual's share of full-size
  paragraphs dropped (fast engine 71.3% to 60.6%): the 111 new paragraphs
  are cells that used to be fused, each now hitting defect 1.
- Ligatures come from HarfBuzz's fallback shaping: a font without a GSUB
  table (Word subsets drop it) gets ligatures composed from the U+FB0x
  codes of its character map. Type 1 and CFF fonts can't be edited; they
  are reused only if their ligature glyphs are drawn.
- A symbol a reused font can't draw is taken from MuPDF's fallback chain;
  for NIST's bullets that embedded Noto Serif whole (59 KB, it isn't
  subset), raising the output from 1.08x to 1.28x.
- Seen on NIST, for phase 4: list items lose their hanging indent, and
  consecutive footnotes are merged into one line.

## Consequences

- Each fix has a number to move, measured before and after, on four
  document types instead of one.
- The corpus needs a decision on storage: the manual is third-party
  copyrighted material and stays out of the repository.
- Rendering characters to test font coverage adds work per font; it is done
  once per font and character, not per paragraph.
- Defect 1 accounts for most of the font reduction and stays in the tool
  until phase 4: outputs keep shrinking 7% to 23% of paragraphs meanwhile.

## Not covered

- There is no metric for the quality of the translation itself; that is
  still judged by reading the output.
- Subscripts and superscripts in formulas are flattened (seen on arXiv, not
  measured).
- The uncommitted terminology work (`terms.py`, glossary enforcement) is
  independent of this plan.
