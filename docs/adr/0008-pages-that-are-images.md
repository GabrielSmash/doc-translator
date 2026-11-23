# ADR 0008: Pages that are images (comics, scanned documents)

- **Status:** Accepted
- **Date:** 2026-11-23

## Context

A comic book (25 pages, Spider-Man, 1960s) came as a PDF of page images:
one JPEG per page, no text layer. The pipeline had nothing to translate
and refused the document ("no text layer (scanned?); run OCR on it
first"). Scanned manuals are the same case.

A prototype on one page showed the whole path works on a laptop:

| Step | Page 5 of the comic |
|---|---|
| OCR (RapidOCR, ONNX, CPU) | 52 of 52 lines, ~5 s a page; most spaces lost ("THEX-RAYSSHOW") |
| Spaces put back with the English dictionary | 13 of 13 balloons right but for OCR misreadings ("L MUST") |
| Balloons: the flat region around the letters | 13 of 13 balloons and captions |
| Erase: paint the letters with the balloon's color | clean, outline and tail kept, no inpainting model |
| Translate: the LLM engine, told it is a comic | colloquial speech ("Eles me odeiam! Querem me impedir de fazer meu trabalho!") |
| Typeset: comic font inside the balloon | in place, centered, uppercase |

## Decision

1. **The same product, optional:** a subpackage `doc_translator/images`
   with an extra (`pip install doc-translator[images]`): RapidOCR
   (`rapidocr-onnxruntime`) and OpenCV (`opencv-python-headless`), both
   Apache-2.0. The translation engines, glossary, cache and checks are
   shared. A page with no text and one image covering it goes this way.
2. **Text over flat color only:** balloons and captions. Titles and sound
   effects drawn over the art are left as they are.
3. **Font:** Comic Neue Bold (SIL OFL 1.1), shipped with the package.
4. **Names of characters, places and teams stay as in the original**;
   other renderings come from `--glossary`. In capitals, case tells
   nothing: candidates are phrases of 2 or 3 uncommon words that recur,
   and a title before a word ("DR. OCTOPUS") (`terms.repeated_phrases`);
   the model says which are names (not how to render them: asked that, it
   wrote "Dr. Aranha", "Dr. Ocupo"). Their words are asked capitalized
   ("Doctor Octopus"), the rest of a text in capitals in sentence case.
5. **Steps** (`images/`): `ocr.py` (lines), `text.py` (spaces back, lines
   joined), `balloons.py` (regions, shape, reading order), `clean.py`
   (erase), `typeset.py` (write), `pages.py` (the pages of a document).
   Balloon texts go to the engine as units of kind "balloon" in reading
   order, the whole comic in one call; the LLM engine is told they are
   comic speech and captions.
6. **Balloons:** the flooded region of a line's surroundings, seeded
   above and below the line (not in a letter's hole). A region filling
   less than 55% of its box leaked into its panel through a gap (a
   balloon fills 70% or more): only the text's box counts then. Reading
   order: panel by panel (panels are what the white gutters separate),
   rows of overlapping heights, left to right.
7. **Checks:** a synthetic comic page (`tests/test_images.py`): balloons
   in reading order, caption told from balloon, letters erased and
   outline kept, the translation written in the balloons.

## Decisions taken

- **2026-11-23:** by the user: the same project with an optional extra;
  RapidOCR and OpenCV; Comic Neue shipped; names kept as in the original.
