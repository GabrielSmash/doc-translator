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

## Revision 2026-11-24: spaces put back by how much words are used

On the whole comic, putting the spaces back with the dictionary alone
chose long words, however rare: "MYNA ME", "THEW ALL", "COMET RUE", "MANI
SOT TO OCTAVIO US". The model translated the misreading ("NO BURRO").

- **Word ranks:** words are now chosen by how much they are used. The
  ranks come from wordninja's list of 126k English words by frequency in
  Wikipedia (MIT), pinned to a commit and checked against its SHA-256,
  downloaded once like the dictionaries.
- **Cost:** a split costs the sum of log(rank × log N) of its words
  (Zipf's law); the cheapest one is kept.
- **The dictionary vets the list:** only words the dictionary has are
  ranked. The list has frequent misspellings and joined words ("noone",
  "thankyou").
- **Speech:** Wikipedia has little of it ("me" is its 600th word). The
  language's common words count as used at least as the 100th, or
  "TOME" would beat "TO ME".
- **Apostrophes:** an apostrophe ends a word or joins an ending to it
  ("OCTOPUS' ARMS", "WIRING'LL"); it does not start one.
- **Misread "L":** a lone "L" is read as "I" ("I MUST RETURN").

Result on the comic's 245 balloons: 60 spaced differently. About 50
became right ("MY NAME", "THE WALL", "COME TRUE", "MAN IS OTTO", "FAR
MORE", "I'VE GOT TO"). About 6 are ambiguous or worse ("POSTS ENTRIES",
"FOUND AWAY", "ID ARE NOT").

### Decisions taken

- **2026-11-24:** by the user: the word list downloaded and pinned, with no
  new package.


## Revision 2026-11-24: one region, several texts; text left as drawn

Two pages reported by the user:

- **Page 6:** a caption and the thought balloon under it became one text,
  lettered outside the panel. The thought balloon's outline has gaps, so
  its inside, the panel and the caption are one flooded region; the
  seeds above its lines also reach the panel.
- **The cover:** the sky under the web was one region holding the Comics
  Code stamp, the date, the web address and the starburst blurb: one
  text, painted blue over the art and lettered in a corner. The logo's
  "the" was lettered "O".

Decisions:

- **Smallest region:** a line belongs to the smallest region that holds
  it, not to the one most of its seeds reach.
- **Texts within a region:**
  - Lines are one text when they are near each other and no outline runs
    between them: one under the other with overlapping extents, or side
    by side on a row.
  - Each text gets the part of the region on its side, cut halfway to
    the other texts.
  - This replaces the columns of touching balloons.
- **Line order:** a text's lines are read in rows, left to right. Lines
  sharing three quarters of their height are one row ("AND NOW" "BEGIN!").
- **Display text:** an open text with letters more than twice the comic's
  usual lettering height is a title, a logo or a blurb over the art. It is
  left as drawn.
- **Unchanged text:** a text the translation leaves as it is (a door number,
  a web address) keeps its original letters: it is neither erased nor
  lettered again.

On the whole comic, the texts found changed only where two texts had been
joined:
- p14: "…SAY, WHAT'S" and "AND THERE'S NO ONE…";
- p8: "WELL, WELL!";
- p6: the thought balloon;
- p1: the cover's texts.

## Revision 2026-11-25: the lettering measured

The style of the comic's 251 texts was measured: letter color, height of
the capitals, stroke and slant.

- **Color:** the letters of every balloon and caption are dark. Only the
  cover's starburst (red on yellow) and an ad (blue) are not. The
  translation was always lettered black.
- **Size:** the capitals fill 61% of their OCR box. The translation was
  sized from the box with a guessed share, and lettered about 6% larger
  than the original.
- **Stroke and slant:** these do not vary from balloon to balloon. The
  plain lettering's stroke is 17–18% of its height everywhere. What is
  bold and slanted are emphasized words inside a balloon ("MY **ARMS** DID
  IT!"), in 31 of 235 balloons at least.

Decisions:

- **Color:** each balloon's letters are measured: the pixels far from its
  color, at the middle of their strokes. The translation is lettered in
  that color.
- **Size:** the capitals' height is measured from the letter shapes in
  each line, leaving out the ones touching the line's edges (an outline).
  The translation's capitals are as tall: a size of height / 0.68, which
  is Comic Neue's capital height, measured on its "H".
- **Bold and italic per balloon:** not detected. Emphasis is per word.

### Emphasized words

Measured word by word (3268 words), the emphasized ones form a group of
their own:
- stroke: 26–30% of the capitals' height, against 18–19% for plain words;
- slant: 14–25 degrees to the right, against within 8 degrees.

Words leaning with a plain stroke are mostly short words, whose slant
cannot be measured well. Heavy upright words are display text.

Decisions:

- **Words:** the OCR gives lines and drops spaces. A line's words are
  its letters closer than 35% of the capitals' height to each other, row
  by row. Blank columns are not used: slanted letters leave none between
  words ("STRENGTH IS INCALCULABLE!" was one word). Shapes shorter than
  half the capitals are slivers of the lines above and below, cut by the
  line's box.
- **Emphasized:** a stroke at least 1.3 times the comic's plain stroke,
  and a slant of 8 degrees or more (Comic Neue Bold Italic leans 9).
- **To the text:** the line text's letters are spread evenly over its
  words' ink. They are matched to the joined and spaced text letter by
  letter. A word is emphasized when most of its letters are.
- **To the translation:** the emphasized phrases are the unit's
  `segments`, the path bold and links take in a document. Each engine
  translates them on their own. `align.locate` finds them in the
  translation, and they are lettered in Comic Neue Bold Italic (SIL OFL
  1.1, same family). It is as heavy as the Bold, so it is outlined in its
  own color by 5% of its size. Its stroke becomes 1.4 times the plain one,
  as in the comic. Read again, the lettered translation's emphasis is found
  by the same measure (a test). A phrase not found is counted in the run summary
  ("Emphasis lost").

On the comic: 243 phrases in 251 texts. On page 6, the thought balloon
"**I DID IT!!** MY **ARMS** DID IT! ... **INCALCULABLE!** ... **ANYTHING!!**"
is found word for word, and 17 of the page's 18 phrases are found in the
translation.
