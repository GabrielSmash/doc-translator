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

## Revision 2026-11-25: page 6 as the user saw it

The user reported page 6:

- "MINUTES LATER..." was left in English.
- Squares of the balloon's color were painted over the outlines.
- An "I" was left beside "JÁ OUVI".
- "I5THAT", "151T" and "1.NOO10OW" were translated as they were.

Causes and decisions:

- **Tight captions:** a region had to be twice its line's box to hold the
  line; a caption hugging its text is less ("MINUTES LATER...": 1.95).
  The rule only keeps out letters' holes and gaps between letters, which
  are much smaller. It is now 1.5. On the comic this found 12 more texts,
  most of them captions ("SECONDS LATER", "AND SO..").
- **Text placed too high:** a caption's text can sit closer to its top
  than the lettering margin allows. Centered there, the translation was
  lettered small. Failing that center, the middle of the room is tried.
- **Erasing open balloons:** an open balloon's lines were erased box by
  box, and the boxes cross the outline where the text runs close to it.
  - A first fix painted the boxes only within the balloon's region, a
    letter height wider sideways to catch letters the OCR left out ("I").
  - The user then saw two new defects: corners painted over the panel's
    light green, and the top of a character's hair cut off under a
    balloon. The region of an open balloon runs into the panel: light art
    (a light background, a face) is part of it, and dark art (hair) is a
    hole in it.
  - An open balloon's letters are now painted one by one: shapes of ink
    at least half in its lines' boxes, plus the marks near the text as
    small as a letter. A shape running out of the boxes (hair) is art, and
    stays.
- **Marks left out of lines:** in a closed balloon, ink near the text is
  erased too when it is as small as a letter (the dots of "LATER...").
  - A first version erased all ink within a line height of the text. It
    painted a wall white: the light blue wall was part of the two
    balloons' region, reached through their tails.
  - Shapes larger than a letter, or running further, are outlines and art,
    and are kept.
- **Digits read for letters:** hand lettering's I, S, O and B are read as
  1, 5, 0 and 8 ("I5THAT", "151T"). Every number of the original must be
  in the translation, so the model had to keep them. In a word of letters
  and digits, the digits are read as letters when that makes known words.
- **Lines read unsure:** a line read with a confidence under 0.85 is read
  again from its box with a third of its height around it. The new
  reading is kept when it is surer and at least 0.85 ("1.NOO10O" became
  "OUT!DON'T"; the door's "1201" became "L!" at 0.68 and was not kept).
  On the comic, 38 of 1337 lines are read again.
- **Two balloons side by side** ("WHAT IS IT? YOU-- OH, NO! NO!" and "HA-HA!
  YOU DON'T BELIEVE..."):
  - Lines on one row are one text only when no outline runs between them.
    Lines one under the other must share a quarter of the narrower one's
    width: a corner is not enough.
  - In a row, balloons are ordered by their texts, not their regions; the
    region of the right one started left of both.
  - Two captions on pages 15 and 18 are now read before the balloons
    under them.

## Revision 2026-11-25: a text is lettered again only where it was erased

The whole comic run after the page 6 fixes showed the cover's "JULY"
still drawn, with "JULHO" lettered small beside it.

- **Cause:** "JULY" is in a small white box over the sky, and its line went
  to the sky's region. An open balloon's color was taken from its region:
  the sky's blue. Against blue, the whole white box is "ink", one large
  shape. Painted letter by letter, nothing of it was erased; painted box
  by box, as before, the box was painted blue.
- **Color:** an open balloon's color is now the most common one around
  its text, in its region or not. On the comic this changes 3 of 105 open
  texts: "JULY" (white), the cover's question mark (red), and an OCR
  misreading.
- **Erased before lettered:** a text is erased only when the painting
  covers 80% of the ink in the middle of its lines' boxes (the ends can
  cross the outline). Otherwise it is left as drawn, untranslated, and
  counted in the run summary ("Left untranslated"). Lettering a
  translation over letters that were not erased is the defect class; this
  closes it whatever the cause.
- On the comic, 8 of 265 texts are left this way, all of them logos,
  titles, stamps or OCR misreadings: "the", "AMAZING",
  "WWW.MARVEL.COM", "JULY", the question mark's blurb, "Man".

## Revision 2026-11-25: possessives, prices and slanted text

The user reported three defects on the first pages:

- **"AMÉRICA'S MAIS EMOCIONANTE SUPER-HERÓI ... MUNDO'S":** the model gave
  Portuguese words the English "'s".
  - A check now rejects a translation with an "'s" word the source does
    not have as it is (names copied whole, "McDONALD'S", stay). Such a
    translation is retried.
  - On the last try, the words are named with the other words that do
    not exist. Since the 8b model kept the "'s" even when named, the
    rewrite is also spelled out ("the world's greatest hero" as "the
    greatest hero of the world").
  - Now: "...SUPER-HERÓI DA AMÉRICA CAPTURADO PELO MAIS TEMIDO SUPER-VILÃO
    DO MUNDO!".
- **"MARVEL COMICS 12 GRUPO":** the cover's "12¢" was read "12t".
  - The reading again of unsure lines kept "12" (0.97): a reading that
    only drops characters is no longer taken.
  - A one- or two-digit number with a letter read for the cents sign
    ("12t") is written "12¢". The box translated is then unchanged and
    stays as drawn.
- **The arrows of page 2 left in English:** their text is slanted (-13
  and 8 degrees).
  - The lines' upright boxes are twice as tall as the letters. Taken for
    a title by DISPLAY_HEIGHT, the text was left as drawn.
  - The OCR gives each line as a quadrilateral. A line's angle and its
    height across it are now kept.
  - A balloon's line height is measured across its lines. A slanted
    balloon's room is turned level around its text, the translation is
    fitted as any, and written turned back by the same angle.
  - The capitals' height and the emphasis are measured on upright letters
    and are left out for slanted text.

## Revision 2026-11-25: dark balloons, lettering on the page, lines the OCR missed

The user reported more text left in English on the cover and page 2, and
asked that each fix serve the class of the defect, not the case.

- **Light letters on a dark balloon** (white on the cover's red question
  mark):
  - Balloons were found only as regions of light pixels. A dark balloon's
    region is now flooded by its color. This is used when, on both sides of
    a line, most seeds are of a middle tone: black seeds are outlines and
    letters, and one side can be out of a light balloon that hugs its text
    (page 9).
  - Tight light letters cut a dark balloon into strips. Regions of one
    color whose lines are one text are joined.
  - Within such a balloon, an outline is what is darker than its color.
  - Only lettering is read there: dark art holds page numbers and marks.
- **Lettering on the page or a panel's background** ("FANTASY! THRILLS!",
  the credits, captions in the margin):
  - The background, larger than MAX_PAGE_SHARE, was always taken for art.
    A line on it is now read when the background holds nearly all of a
    ring half a line height around it, other lines left out of the ring.
  - It must also be lettering: three letters or more, 80% of them
    capitals. A web address, a watermark, a page number and readers'
    letters are print, and are not read.
- **Lines the OCR missed** ("OCTOPUS??", slanted more than the rest of its
  balloon; "WITH HIM? HE DIDN'T ANSWER!"; "I'M HELPLESS!"):
  - Their letters were neither translated nor erased whole.
  - `images/missed.py` looks near each balloon's text, on its color, for a
    row of three letters or more that no line of the page holds. A
    slanted line's area is the OCR's quadrilateral, not its upright box.
  - It reads that band again across the balloon's text, turned level at
    the balloon's slant. Lines read before are left out, a letter
    different or not.
  - The line goes to the balloon whose text is nearest, in reading order
    by the row found. It is erased where it was read, turned back onto
    the page.
  - On the comic: 5 lines recovered, all of them real.

## Revision 2026-11-25: checks that fail after the retries; look-alike letters

The possessive came back on a run of four pages ("AMÉRICA'S… MUNDO'S").
The fix had been a check and a retry, and a retry depends on the model
following it. The user asked for logic that holds for the class, not for
the case.

- **A last strategy before warning:** a translation still failing a grave
  check after the retries (left untranslated, an English possessive,
  profanity) is translated once more alone, with a short prompt: its kind
  and the names it holds, without the description, context or batch.
  - Tried directly, the 8b model kept the "'s" in the long prompt and
    dropped it alone, in 4 of 4 texts.
  - The lesser of the two by the checks is kept.
  - Now: "…CAPTURADO PELO SUPER-VILÃO MAIS TEMIDO DO MUNDO!", "PALESTRA DO
    TORCH".
- **Register:** "BIG DEAL!" came back "QUE BOSTA!". Profanity in a
  translation whose source has none is a grave check. It is named on the
  last try, and the fallback above applies (now "GRANDE COISA!").
- **A grammar review by the model** was tried and dropped:
  - In batches, the 8b model corrected 1 of 47 translations, and for the
    worse.
  - On 5 known errors, it corrected none in either capitals or sentence
    case, and invented "TRUZER".
  - The 4b model in sentence case corrected 2.
  - Gender agreement ("A MAIS INTERESSANTE SUPER-HERÓI") is still open.
- **Look-alike characters:** the class of "I5THAT" (digits for letters)
  now holds a J or an "!" for an I ("SPEAK-JNG", "!T'S"). An "!" counts
  only when not after a letter, and when its word is a dictionary word.
  Initials stay ("J.J.", "J. JONAH").
- **Common letters read for others** (O for D, L for I): tried one at a
  time, and taken when the run becomes much likelier by word use.
  - The gain must be 10 or more, on runs of 5 letters or more. On the
    comic the right swaps gained 20 or more, the wrong ones 4 to 7.
  - Now: "DESIGNED", "SPIDER-MAN", "BORED WITH", "STUDIES", "FINDS".
- **A thought balloon's bubbles** are as small as a letter, and were
  erased as an "O" left out. A mark left out must be on the row of one of
  the balloon's lines.

## Revision 2026-11-25: the comic's characters; lettering where the text was

- **Characters:** the user proposed reading the whole comic first and
  giving the model a general context. Before translating, the comic's
  text is read in parts of 6000 characters, each completing a list of
  characters: name, gender, who they are. The list goes with every batch,
  with the rule that words for a character agree with its gender.
  - On four pages, the list came out right ("Peter Parker: male;
    Spider-Man…").
  - The cover's blurb still came out feminine ("A MAIS INTERESSANTE
    HERÓI…"), and worse than before. The 8b model's answers vary from run
    to run, and the context does not fix agreement.
  - Agreement stays open. A deterministic check (a grammar checker) is the
    user's decision: it would be a new dependency.
- **Lettering in two joined balloons** (page 14): the room was one region,
  and lines centered on it fell on the narrow join, lettered small. Each
  line now goes to the part of the room that held the original text at
  its height: the text runs from one balloon to the other, as drawn.
- **A mark left out** must be on a line's row and within a line height of
  its ends. A thought balloon's bubble at the height of a line of the
  balloon beside it was erased (page 14).

## Revision 2026-11-25: grammar checked by LanguageTool

The user chose LanguageTool to close the class of agreement errors the
model makes and cannot see ("A SUPER-HERÓI", "SUA DESEJO", "MEUS BRAÇOS
NÃO É", "MINHA COSTAS").

- **The server:** LanguageTool 6.6 (LGPL 2.1, Java) is downloaded once,
  ~250 MB, pinned and checked against its SHA-256 like the dictionaries.
  It runs on this machine for the length of a run. Without Java, a
  warning is logged and translations are not checked for grammar. No
  Python package is added.
- **Rules used:** grammar, syntax, confused words and semantics. Its
  spelling, capitalization and style rules mistake the comic's names and
  capitals for errors. Capitals are checked in sentence case, with the
  same offsets.
- **Measured** on the whole comic's translations: 4 matches in 451 texts,
  all of them real errors.
- **Rules of our own** (`translators/grammar_pt.xml`, installed as its
  `grammar_custom.xml`):
  - LanguageTool misses an article whose noun comes after an adjective:
    "A MAIS INTERESSANTE SUPER-HERÓI", "A MELHOR SUPER-HERÓI", the
    superlatives the model writes for the hero.
  - The rules cover article (+ mais/menos) + adjective + a noun of the
    other gender.
  - Before "melhor", LanguageTool tags "a" otherwise, so "a" and "o" are
    matched as words.
  - Set phrases stay: "A LONGO PRAZO", "A TODO CUSTO", "VOU A PÉ".
- **In the translation:** a balloon translation with grammar errors is
  translated again with the errors named. If they remain, it is translated
  alone with a short prompt. If they still remain, LanguageTool's own
  suggestions are taken. Each candidate must be free of grammar errors and
  no worse by the other checks.
- **Two more checks**, found on the cover's garbled blurb ("O MAIS
  FASCINANTE HERÓI SUPER-HERÓI… TEMIDO VILLAIN SUPER-HERÓI"), which passed
  every check:
  - a part of a hyphenated source word left untranslated ("VILLAIN" of
    "SUPER-VILLAIN");
  - a word repeated with only a space between ("HERÓI SUPER-HERÓI") when
    the source repeats none.
  Both are grave: retried, then translated alone. Neither matched
  anything else in the whole comic.
- **Words that still do not exist** ("VILLÃO", named to the model and
  kept) get the translation alone too. As the last step,
  LanguageTool's spelling suggestion is taken when it is a dictionary word
  one letter away ("VILÃO"). A suggestion further away is another word
  ("REINSERTADO" is not "REINSERÇÃO").

### Revision: texts left out or joined on the whole comic

The whole comic, run with every check, showed six texts read wrong. Each
had a cause shared by other texts; each rule was checked on all 25 pages,
and changed only the texts it was made for.

- **A caption on a panel's frame** ("AND, NOT FAR AWAY...", "AND SO...")
  is on the page's background, and lettering there is read only with no
  art around it. The frame's straight line counted as art. Thin straight
  lines three line heights long are frames, left out of that test; a block
  of art as long is not thin.
- **Two balloons touching** where their outlines meet on one row: the OCR
  read that row across both ("...ATOMIC POWER MY STRENGTH! I'LL"), and the
  line joined the two texts into one balloon; the other was left blank. A
  line running across two texts side by side, each of two lines or more,
  is cut between them, and each half is read again and goes to its own
  text.
- **A sound effect beside a balloon** ("BR-I-I-NG", 18 degrees) was joined
  to the balloon's text, through the panel its region leaks into. A text's
  lines are parallel: on the comic they differ by 4 degrees at most.
  Lines differing by more than 8 are not one text.
- **The room of an open balloon** ran onto the panel around it (a thought
  balloon whose outline has gaps, on a light panel), and the translation
  was lettered out of the balloon. The room is now the balloon's own color
  near its text.
- **A letter set apart at a line's end** ("LATER" read "ATER": the
  caption's first letter is lettered larger; "I NEVER", "I HAD TO") was
  left out of the OCR's box. A letter-sized shape next to a line's end,
  on its row and in no other line, widens the box. Read again wider, the
  line is read less surely ("SChOOL"), so only the letters added at its
  end are taken.
- **The price** ("12t" for "12¢") was joined to the next line's word
  ("12tGROUP") before it was read as a price; it is now read as one after
  the spaces are put back.

### Revision: translations that say something else

Twice in seven pages the local model wrote a fluent translation of
something the balloon does not say: the cover's "AMERICA'S MOST EXCITING
SUPER-HERO CAPTURED BY THE WORLD'S MOST DREADED SUPER-VILLAIN!" became "A
MAIS FAMOSA ESTAÇÃO DE RÁDIO DO MUNDO!", and "AW, SHADDUP! WHOEVER
EXPECTED SPIDER-MAN TO BUTT IN!" got the next balloon's translation. Every
check looks at the form (English left, grammar, invented words, numbers),
and both passed.

- **The reference:** the fast engine (Opus-MT, already a dependency, run
  on the CPU) translates every balloon once, the glossary's names kept.
  Its style is poor, but it does not change the subject.
- **The test:** the share of the reference's content words (accents
  folded, their first four letters, function words and kept names left
  out) the translation has. Below 25%, on references of three words or
  more, it says something else. On the seven pages, the two invented
  translations shared 0% and 12%; the good ones, 29% or more.
- **Then:** translated again with what it said named, then alone with a
  short prompt; a candidate is taken when it shares enough and has no
  grave problem. A candidate may not leave words in English the first
  translated, unless the reference keeps them too: the reference can
  differ by a name alone ("HOMEM-ARANHA" for the "SPIDER-MAN" the model
  keeps), and a retry then kept "SUPER-THRILLER" where the first had
  "ROMANCE". Failing that, the translation sharing the most is kept, with
  a warning.
- Only comics: on documents the 4b model's translations were not seen to
  drift, and the check was not measured there.

### Revision: characters' names kept as written

The user's decision: names stay as the original writes them ("SPIDER-MAN",
"HUMAN TORCH", "FANTASTIC FOUR"). Left to the model, a run kept them in one
balloon and translated them in another ("O FOGO DO TORCEDOR", "TORCIDA" for
"TORCH").

- The cast sheet (read from the whole comic first) gives each character's
  name, the other names the text calls them by, and whether the name is a
  proper name: a role ("the governor", "the security police") is
  translated. Kept, "THE GOVERNOR" was "O GOVERNOR".
- Each name is kept as the text writes it, an article before it left out
  ("the Torch"); the OCR's forms ("SPIDER MAN", "DR.OCTOPUS") are written
  as the sheet does ("SPIDER-MAN").
- The sheet lists a short form in one run and not in the next: the last
  word of a name, used alone after "the" ("THE TORCH'S LECTURE"), is kept
  too.
- A retry for the meaning is taken only when no worse by the other checks
  than the first: one said what the balloon says and translated the name
  kept ("HOMEM-ARANHA").
- The sheet called things characters with proper names ("THIEVES",
  "MOUSE": "ERA SÓ UM MOUSE"). A name of one word that is a word of the
  source language is translated when the texts use it as a thing: after
  "a" ("JUST A MOUSE") or plural. The dictionary alone cannot tell: it has
  "charlie" and "hulk", which the texts use as names.
