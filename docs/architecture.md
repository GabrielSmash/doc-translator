# Architecture

This document describes how doc-translator is built today, the architecture
it should move to, and everything that has to change to get there. The
defects it refers to by number are the ones measured in
[ADR 0001](adr/0001-pipeline-audit-and-correction-plan.md).

## 1. What the tool must guarantee

1. **Content is never corrupted.** A character, number, link, image or
   drawing of the original is either preserved or deliberately replaced by
   its translation. Dropping one silently is the worst failure.
2. **Layout is preserved.** Text stays where it was, in the same font, size
   and style, as far as the translated text allows.
3. **Translation is local and free.** No paid API; nothing leaves the machine.
4. **Every claim is measured.** A layout rule is accepted only after it is
   run on several document types and compared with the identity run.

The order matters: when two of them conflict, the lower number wins.

## 2. Current architecture

```text
cli.py ── builds the translator, calls ──► pipeline.translate_pdf
                                             │
  extract.py   page ─► list[Paragraph]       │ 1. extract
  align.py     Paragraph ─► runs, segments   │ 2. prepare
  translators/ Unit ─► UnitTranslation       │ 3. translate
  align.py     translation ─► styled runs    │ 4. realign
  fonts.py     which fonts can be reused     │ 5. fonts
  render.py    redact + insert_htmlbox       │ 6. render
                                             ▼
                                        output PDF
```

| Module | Responsibility today |
|---|---|
| `models.py` | `Span`, `Line`, `Paragraph`, `Leader` |
| `extract.py` | Reads PyMuPDF spans, detects links, underlines, small caps, code, list markers, TOC leaders, alignment, and groups lines into paragraphs |
| `align.py` | Merges spans into styled runs; maps styles onto the translation by fuzzy matching |
| `terms.py` | Finds recurring terms of the document (uncommitted) |
| `translators/base.py` | `Translator` protocol, `Unit`, `Glossary`, placeholder protection |
| `translators/opus.py` | Opus-MT on CTranslate2, sentence by sentence |
| `translators/llm.py` | Ollama: terminology, batches, checks, styled phrases |
| `fonts.py` | Reuses embedded fonts; picks a generic fallback family |
| `render.py` | Removes the original text, computes the insertion box, writes HTML, recreates links and TOC leaders |
| `pipeline.py` | Wires the stages for one document |
| `cli.py` | Arguments, engine choice, model download |

### Structural problems

These are separate from the nine measured defects: they are the reasons the
defects were easy to introduce and hard to see.

| # | Problem | Where | Effect |
|---|---|---|---|
| S1 | The model only knows text that will be translated | `models.py`, `extract.py` | Code, list markers, rotated text and table rules are skipped or dropped inside `extract.py` without leaving a trace, so nothing can report what was left out or protect it during rendering |
| S2 | One module reads, classifies and groups | `extract.py` | A rule for underlines breaks table detection (defect 2); each heuristic can only be tested through the whole extraction |
| S3 | Placement and drawing are one step | `render.py` | The box, the font and the scale are decided while writing to the page, so a placement decision cannot be tested or measured without producing a PDF |
| S4 | No report | `pipeline.translate_pdf` returns `None` | Reduced fonts, fallback fonts and suspicious translations exist only as log lines |
| S5 | Checks belong to one engine | `llm.py` (`_problems`, `_match_case`) | The fast engine has no number check (defect 9); `_mostly_upper` is duplicated in `opus.py` and `llm.py` |
| S6 | Language pair is hardcoded | `cli.py`, `llm.py` prompts and `MINOR_WORDS`, `terms.py` stopwords | Adding a second pair means editing five places |
| S7 | Shared concepts live in the wrong place | `Glossary` in `translators/base.py`; `MONO_HINTS` in both `extract.py` and `fonts.py` | Two definitions of "monospaced" that can drift apart |
| S8 | Any error aborts the document | `pipeline.py` | One failing page or one Ollama timeout discards a nine-minute run |
| S9 | Translations are not kept | `llm.py` keeps a cache in memory only | Every layout experiment on the LLM engine costs a full re-translation |
| S10 | Validation by eye | no integration tests, no CI | Pages 17 and 29 of the reference manual were broken and unnoticed |

## 3. Target architecture

### Stages and contracts

Each stage takes and returns plain data, so it can be tested and measured on
its own. Only the first and the last touch a PDF.

```text
            ┌──────────┐  PageContent   ┌──────────┐  list[Block]
 PDF page ─►│ 1 read   ├───────────────►│ 2 analyze├──────────────┐
            └──────────┘                └──────────┘              │
                                                                  ▼
            ┌──────────┐ list[Translated]┌───────────┐   blocks to translate
            │ 4 align  │◄───────────────┤3 translate │◄─────────────┘
            └────┬─────┘                └───────────┘
                 ▼                       engine + shared checks
            ┌──────────┐ list[Placement] ┌──────────┐
            │ 5 layout ├────────────────►│ 6 draw   ├─► PDF page
            └────┬─────┘                 └──────────┘
                 └──────────────► Report (every stage adds to it)
```

| Stage | Input → output | Rule |
|---|---|---|
| 1. Read | page → `PageContent`: text lines, strokes, fills, links, images | Faithful copy of the page; no interpretation |
| 2. Analyze | `PageContent` → `list[Block]` | Every text line ends up in exactly one block; each block has a kind and an action |
| 3. Translate | blocks with action `translate` → translations | Engines differ; checks and retries are shared |
| 4. Align | translation + styled phrases → styled runs | Pure function; no PDF, no model |
| 5. Layout | block + runs + obstacles → `Placement` (box, fonts, size, scale) | Decides everything about where and how; draws nothing |
| 6. Draw | `Placement` → page | Executes the placement; decides nothing |

### Document model

The central change is S1: the model describes **everything on the page**,
and each block says what will be done with it.

| Field | Values |
|---|---|
| `kind` | paragraph, heading, list item, table cell, TOC entry, caption, code, marker, rotated text |
| `action` | `translate`, `keep` (left untouched), `skip` (reported, not handled) |
| structure | lines, spans, alignment, prefix, leader, direction |

Consequences:

- Stage 6 knows which areas are `keep` and never removes them.
- Table rules, fills and underlines are classified once, in stage 2, as
  page elements with a role, so an underline rule and a table rule cannot
  disagree.
- The report can state, per page, how much text was translated, kept and
  skipped.

### Target module layout

```text
src/doc_translator/
  cli.py
  pipeline.py          orchestration, per-page error isolation, Report
  report.py            Report: coverage, reductions, fonts, warnings
  languages.py         LanguagePair: codes, model, prompts, minor words
  glossary.py          Glossary (moved from translators/base.py)
  terms.py
  pdf/
    read.py            stage 1
    draw.py            stage 6
    fonts.py           reuse by ink test, fallback from span flags
  analysis/
    model.py           PageContent, Block, Span, Line
    lines.py           spans → visual lines, small caps, sub/superscript
    graphics.py        strokes and fills → underline, table rule, background
    blocks.py          grouping into blocks; tables; TOC
    classify.py        kind and action of each block
  translation/
    base.py            Translator protocol, Unit
    protect.py         placeholders
    checks.py          numbers, length, casing, glossary (all engines)
    cache.py           translations on disk
    opus.py
    llm.py
  align.py             stage 4
  layout.py            stage 5
```

## 4. Everything that has to change

### 4.1 Correctness (ADR 0001)

| Defect | Change | Stage |
|---|---|---|
| 3 | Font coverage decided by rendering the character and checking for ink | `pdf/fonts.py` |
| 2 | Strokes classified by role; a stroke is an underline only when it matches the extent of the text. No removal of line art by touch | `analysis/graphics.py`, `pdf/draw.py` |
| 1 | Text bounded by baselines and real obstacles, not by neighboring line boxes | `layout.py` |
| 4 | Fallback family from known family names, then the PDF flags (done in phase 1) | `pdf/fonts.py` |
| 5 | Links saved before redaction; those not recreated are restored | `pdf/draw.py` |
| 6 | Lines whose direction is not horizontal get action `keep` | `analysis/classify.py` |
| 7 | Save with `garbage=4` | `pipeline.py` |
| 8 | Styled phrases translated and matched, as the fast engine does; the location step is removed | `translation/llm.py` |
| 9 | Number check applied to every engine | `translation/checks.py` |
| 10 | Text placed over a fill keeps inside it, or its color follows the background it lands on | `layout.py` |

### 4.2 Structure

| Change | Solves |
|---|---|
| Model with `kind` and `action` for every block on the page | S1 |
| Split `extract.py` into read, lines, graphics, blocks, classify | S2 |
| Split `render.py` into `layout.py` (decides) and `pdf/draw.py` (executes) | S3 |
| `translate_pdf` returns a `Report`; the CLI prints its summary | S4 |
| `checks.py` shared by the engines; one definition of each helper | S5, S7 |
| `LanguagePair` holding codes, model, prompts and word lists | S6 |
| `Glossary` moved to its own module | S7 |

### 4.3 Robustness

| Change | Why |
|---|---|
| Per-page error isolation: a failing page is copied unchanged and reported | S8 |
| Translation cache on disk, keyed by engine, model, glossary and text | S9: a layout change stops costing a re-translation; an interrupted run resumes |
| Explicit detection of documents the tool cannot handle (no text layer, encrypted) with a clear message | Today these are not handled explicitly |
| Sub and superscripts kept as such | Seen flattened on the arXiv paper; not yet measured |

### 4.4 Command line

| Change | Why |
|---|---|
| Summary at the end: pages, share translated, paragraphs reduced, fonts replaced, warnings | The user learns what went wrong without reading logs |
| `--report FILE` writing the report as JSON | Lets the regression harness and users consume it |
| Progress for the LLM engine | A nine-minute run currently shows a line every batch at most |
| Non-zero exit code when a page failed | Usable in scripts |

### 4.5 Testing and continuous integration

| Change | Why |
|---|---|
| Measurement harness in the repository (`tools/measure.py`) | S10. Done in phase 0 |
| Identity run as a test: original text through layout and draw | Separates renderer defects from translation effects |
| Synthetic fixtures generated by script: bordered table, rotated text, subset font without accents, internal links | Cover the measured defects without redistributing third-party documents |
| Public corpus downloaded on demand, not stored | Real documents without copyright issues |
| Thresholds on the metrics as regression tests | A change that lowers a number fails |
| GitHub Actions running the unit tests and the fixtures | There is no CI today |

### 4.6 Distribution and housekeeping

| Change | Why |
|---|---|
| Publish to PyPI | Installation is by cloning the repository today |
| Commit or discard the terminology work (`terms.py`, glossary enforcement) | It is uncommitted and independent of this plan |
| README limitations updated from the audit | They should match what was measured |

## 5. Order of work

Each phase leaves the tool working and has a number to move.

| Phase | Work | Done when |
|---|---|---|
| 0 ✅ | Harness, fixtures and corpus in the repository; CI | The audit numbers are reproduced by one command |
| 1 ✅ | Defects 3 and 2 (content corruption), 7 and 9 (small); 4 moved here | No blank characters on the NIST report; tables on manual pages 17 and 29 intact |
| 2 ✅ | Report and CLI summary; per-page isolation; translation cache | A run states what it changed; a layout experiment does not re-translate |
| 3 ✅ | Model with kinds and actions; `extract.py` split | Every line of every corpus page belongs to one block |
| 4 ✅ | `layout.py` separated from drawing; defects 1, 5, 10 (6 done in phase 3) | Identity run keeps full size in at least 98% of paragraphs |
| 5 ✅ | Defect 8; `LanguagePair`; PyPI | Styles carried over on the LLM engine match the fast engine |
| 6 ✅ | Reading review: layout harmonized, translation context | Pages read as ordered; menus and screens keep their lines |
| 7 ✅ | Defects by class: inspector, causes C1–C6 | Every class counted on the corpus; the reviewed defects at zero |

Phase 0 comes first because every later phase is judged by it. Phase 1 comes
before the restructuring because those defects corrupt content today and
their fixes are local. Defect 1 waits for phase 4: it changes how every
paragraph is placed, which is the highest regression risk in the plan.

### Phase 2 results

| Change | Where | Measured |
|---|---|---|
| Report: per page paragraphs, reduced, not fitted, error; share of text translated; fonts kept and replaced; warnings logged during the run. Printed as a summary, `--report FILE` writes JSON | `report.py`, `cli.py` | Reference manual: 286 reduced paragraphs on 29 pages and all 9 fonts replaced, now visible without `-v` |
| Per-page isolation for reading and drawing: the page is restored from a copy taken before drawing and reported; exit code 1 | `pipeline.py` | Tests force a failure in each stage |
| Translation cache in SQLite, per engine, model, glossary and version; the LLM engine also saves each batch | `translators/cache.py`, `llm.py` | Manual, fast engine: translation 8.2 s → 0 s on the second run, same text. NIST, LLM engine interrupted after 24 of 31 paragraphs: the next run translated only 7 |
| Unusable documents refused with a clear message: not a PDF, password, no text layer | `pipeline.py` | |

Deviations from the plan: `translate_pdf` still returns `Result` (the
harness needs every stage's output) and the CLI builds the `Report` from it.
The cache lives in `translators/` until phase 3 moves the package. A
translation error still stops the run, since it concerns the whole document,
but with the cache the next run resumes instead of starting over.

### Phase 3 results

`extract.py` and `models.py` became `pdf/read.py` (stage 1, `PageContent`)
and the `analysis` package (stage 2): `model.py`, `lines.py`, `graphics.py`,
`blocks.py`, `classify.py`. `Paragraph` is now `Block`, with a `kind` and an
`action`.

| Change | Measured |
|---|---|
| Code, list markers standing apart and rotated text become blocks with action `keep`, instead of being dropped (or, for rotated text, redrawn horizontally) | Manual: 88 markers, 12 code blocks, 2 rotated lines kept; the summary lists them |
| Kept blocks are obstacles for the translations next to them | Overlaps added: NIST 22 to 0, arXiv 9 to 5, manual 13 to 11 |
| Every line of a page belongs to exactly one block | Test on every page of the corpus and fixtures; also holds on all 29 pages of the manual |
| Kinds of translated blocks: heading, list item, table cell, TOC entry, caption, paragraph | Descriptive for now; layout can use them in phase 4. Table cells need three blocks side by side, so two-column tables are labeled paragraphs |
| New metrics `kept_pct` and `kept_lines_lost` (drawing must never remove kept text) | `kept_lines_lost` is 0 on every document |
| One definition of "monospaced" (`fonts.MONO_NAMES`) | S7, in part |

`translated_pct` now counts only text sent to translation, so it dropped
where text is kept on purpose (rotated text fixture 100% to 81%); `kept_pct`
shows the rest. `render.py`, `fonts.py` and `translators/` keep their place
until phase 4 splits layout from drawing.

### Phase 4 results

`render.py` became `layout.py` (stage 5: box, HTML and scale, measured on a
scratch page) and `pdf/draw.py` (stage 6: executes the placements).

| Change | Measured |
|---|---|
| Defect 1: the last line of a paragraph may go down to the letters of the next element (its first baseline minus its ascent) or to the edge of an image or fill, instead of stopping at neighboring line boxes | Identity run, paragraphs at full size: arXiv 78.3% to 98.4%, IRS 65.2% to 99.8%, NIST 98.8% to 100%, manual 99.9%. Pseudo-translation: IRS 60.4% to 93.1%, arXiv 66.7% to 87.6% |
| Line height taken from the largest letters of the paragraph; multi-line paragraphs may grow 3% sideways into free space, so a slightly wider replacement font does not add a line | Part of the numbers above |
| Defect 10: a fill containing the text (a header band) bounds the translation | New fixture `dark_band`: no blank characters with pseudo-translation (1 before) |
| Defect 5: links are read whole (any kind), spans split where a link starts or ends, recreated over the translated words; links not recreated are put back where they were | NIST: internal links lost 6 to 0, URI 2 to 0; fixture `internal_links`: 0. The link metrics now count targets per page, not rectangles |
| All the paragraphs of a page are drawn as one piece of content instead of one Form XObject each | Output size: IRS 3.6x to 1.17x, NIST 1.25x to 1.12x, arXiv 1.24x to 1.08x. Word positions identical to within 0.05 pt |
| First-line indent kept (`text-indent`): hanging indent of list items, text after a run-in heading ("Encoder:") | NIST list items line up after their number again; arXiv run-in headings no longer overprinted |
| Footnotes: a line starting with a small mark starts a new block; small raised spans are superscripts | NIST and arXiv footnotes no longer merged; marks stay raised |
| Lines of symbols only (dot leaders of forms, arrows) are kept | IRS: 1.3% of the text kept, no longer redrawn |

Accepted regressions, all checked on the rendered pages:

- Row correlation of the ink profile drops on a few pages (arXiv
  pseudo-translation mean 0.815 to 0.789; IRS page 2 0.606 to 0.515; arXiv
  page 4 0.931 to 0.872): first-line and hanging indents are now kept, and
  text grows into space it may now use instead of shrinking in place. Each
  page was compared with the previous output: equal or closer to the
  original by eye (arXiv page 4 had paragraphs printed over each other).
- IRS pseudo-translation: translations running longer than the original
  reach the form's dot leaders (overlaps 140 to 141; already there before).
- `below_6pt` counts paragraphs starting with a footnote mark smaller than
  6 pt even when nothing was reduced.

Still open: the time to place text (16 s on the IRS form, 21 s before) is
spent measuring each paragraph on a scratch page.

### Phase 5 results

| Change | Measured |
|---|---|
| Defect 8: the LLM engine translates each styled phrase on its own, in batches after the paragraphs and without context, and `align.py` finds it in the paragraph translation, as for the fast engine. The location step is removed | Manual, styles carried over: 88.9% to 97.2% (fast engine 100%); translation time 532 s to 420 s. Pages 15 to 17 (45 phrases): 84.4% to 100%, 89 s to 59 s, paragraph translations unchanged |
| `LanguagePair` (`languages.py`): language names, target code, Opus-MT model and token, terminology examples for the prompt, source stopwords, target minor words. The engines, the CLI and the harness read it; the prompts it produces are identical to the previous ones | |
| Package ready for PyPI: classifiers, project URLs, license file in the wheel; the wheel installs and runs in a clean environment; README installs with `uv tool install` | Name `doc-translator` free on PyPI |

The three phrases still lost on the manual are wordings the LLM chooses
differently inside the paragraph ("Type:" alone is "Tipo:", in the sentence
"Digite:"; "bold" is "negrito" alone, "destacado" in the sentence). Phrases
placed in the paragraphs' batches, or given their paragraph as context, were
tried first: the model then copied whole paragraphs untranslated (6 instead
of 1 on pages 15 to 17), so they are translated apart.

### Phase 6 results

Reading the translated corpus showed what the metrics did not: pages looked
disordered (each paragraph reduced on its own, to its own size), lines of
menus and terminal screens were joined into one paragraph, and the LLM
translated names of menus and lacked context.

| Change | Measured / seen |
|---|---|
| A line ends its paragraph when the next line's first word would have fit on it (measured in Helvetica, so justified text is not split), when columns are aligned with spaces, or inside a frame (rules above, below and on both sides) with room left | Manual: menus, terminal screens, two-line headers and notes after list items keep their lines. Corpus: the remaining new breaks were checked one by one (author lists, table cells) |
| Text of one style on a page shares one size, rounded down to 5% steps; below 85% a paragraph shrinks alone; text the translation left unchanged keeps its size | Pages read as uniform. Full-size shares under pseudo-translation drop by design (arXiv 87.6% to 62.8%); identity runs unchanged |
| Before shrinking, paragraphs may grow 10% sideways (3% first); vertical rules bound that growth | IRS: text no longer crosses the column rules of the form |
| Text without letters is not sent to the fast engine | IRS page 4: Opus-MT turned "1,020" into "1.020" in some cells and "$700" into "$700." |
| LLM: a description of the document (written once by the model from its headings and opening text), the section heading and three preceding paragraphs go with every batch | Manual pages 15 to 17: fluent text, "Network Setup" and "SNMP Setup" kept |
| Italic names written always the same way recur in the document: kept in English by both engines | "Network Setup" kept; "IP address" in running text still translated |
| LLM checks: a short text answered with a sentence (a shifted batch) and text left mostly in English are rejected; of the batch answer and the retry, the lesser problem is kept | A heading "IP Address" had received the next paragraph's translation |

Models compared on manual pages 15 to 17 (GTX 1650 Ti, 4 GB):

| Model | Time | Notes |
|---|---|---|
| `qwen3:4b-instruct` (default) | 104 s | good; occasional wrong words |
| `qwen3:8b` | 487 s | best wording and terminology; does not fit the GPU |
| `translategemma:4b` | 278 s | fluent, but ignores the glossary ("Network Setup" translated, "traps" as "armadilhas") |

The default stays; `qwen3:8b` is documented for better quality.

### Phase 7: defects by class

The review of phase 6 found four defects by eye: text written over a
paragraph (manual p. 6), table text out of its cells (p. 9, 14), page
numbers of the table of contents one row too low (p. 2). Instead of fixing
each, the cause of each was traced, and every class of defect is now
counted on the whole output by `tools/defects.py`. `measure.py` stores the
counts in the baseline, so a class cannot come back unnoticed, and each class
has a fixture (`split_line`, `ruled_table`, `toc_leaders`) for CI.

| Cause | Class (check) | Fix | Manual, fast engine |
|---|---|---|---|
| C1. PyMuPDF reads a justified line written word by word as one line per word | `split_lines`, `overlapping_text` | Pieces on one baseline are joined when the gap is a space between words (stretched only inside a justified paragraph) | 12 / 4 → 0 / 0 |
| C2. Layout did not know the box of rules around a block | `crossed_rules` | The cell (nearest rules on four sides) bounds the translation | 9 → 1 |
| C3. A one-line block's alignment was always guessed as left | `lost_alignment` | Alignment of its column, or its place in the cell | 17 → 1 |
| C4. Drawing read the drawn page back to place leaders and links, in a box that reaches the next paragraph | `misplaced_leaders` | Layout records where each line was written; drawing uses that | 32 → 0 |
| C5. Classification ignored structure: bold cells became headings (and the LLM's section context) | `headings_in_cells` | A block in a cell next to other cells is a table cell | 20 → 0 |
| C6. Short texts translated as sentences ("no" → "Não.", "Transmit –" → "Transmita...", "N/A" spelled out) | `changed_form`, `grown_cells` | Signs at the ends kept out and put back; final punctuation and first-letter case kept; `N/A` kept by both engines | — → 1 |

The same checks then found, on the IRS form, an item marker "(c)" appended
to the previous item and lost, and dot leaders drawn one dot at a time that
the translation ran over (25 overlaps → 0): both fixed the same way.

## 6. Open decisions

1. **Corpus:** which public documents form it, and whether synthetic
   fixtures alone are enough for CI.
2. **Fit policy once defect 1 is fixed:** when a translation still does not
   fit, reduce the font (today), or ask the LLM engine for a shorter
   wording first. The second needs a measurement of how often it happens.
3. **Monospaced text:** left untranslated today. With `kind` and `action`
   this becomes a setting rather than a rule in the code.
4. **Translation quality metric:** none exists; reading the output is the
   only check. A reference-free quality model would add a dependency and
   has to be checked for license.
5. **Second language pair:** not planned; `LanguagePair` only removes the
   obstacle.
