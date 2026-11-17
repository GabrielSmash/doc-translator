# ADR 0003: Screens are recognized by their content, and their names kept

- **Status:** Accepted
- **Date:** 2026-11-16
- **Cause:** T2 of [the fidelity sweep](../architecture.md#phase-8-translation-fidelity-by-cause)

## Context

The manual reproduces terminal screens: menus (`<1> Network Setup`),
banners (`**** SNMP Setup ****`), prompts (`login:`), time stamps and
aligned columns. A screen was recognized only by a monospaced font. Many are
set in the body font, so their lines were translated as prose.

The reader finds these names on the device, so a translated name points to
something that does not exist. The same name was also translated in one
place and kept in another: "SNMP Setup" kept, "Network Setup" translated as
"Configuração de rede" in the sibling menu item.

The sweep counted 49 screen lines and screen names translated.

## Decision

1. **A screen is recognized by its content** (`analysis/classify.py`,
   `is_screen`): menu items, banners, prompts, time stamps and columns
   aligned by runs of spaces. A block is a screen when at least 2 lines and
   half of its lines look like a screen.
2. **A screen grows to the blocks that follow it** (`continues_screen`):
   same style, same margin, close below (`login:` after the banner).
3. **Screen lines are kept as CODE**: not translated. Only the lines set
   in the style of the screen-like lines (`screen_part`): the PDF can put
   the paragraph about a screen in the screen's block, in the body font,
   and that paragraph is translated.
4. **The names a screen shows are kept everywhere** (`terms.screen_names`,
   through `Unit.names` into `kept_terms`), like the italic interface
   labels. A name the document also writes as ordinary text ("Main Menu"
   and "the main menu") is kept **only with its capitals**, and only if it
   has two words or more: one capitalized word also starts sentences
   ("Exit the menu").
5. **Quoted names an instruction points at are kept where quoted**
   (`terms.quoted_names`): a verb before them ("select ‘View
   configuration’", "will display “226 Transfer complete”") or the kind of
   thing after them ("the ‘Control’ tab", "“User” and “Password” prompts").
   A quoted word that is just a word ("the “subnet mask” of the network")
   is translated.
6. **One source of truth:** `kept_terms` returns a `Glossary` with three
   kinds of kept terms, `keep` (however written), `exact` (with these
   capitals) and `quoted` (between quotation marks). Both engines, the
   cache key, the pseudo engine and the check use it.
7. **The LLM's last try** (ADR 0002) also names kept names the model still
   translated.
8. Check: `translated_names` in `tools/defects.py`, through
   `Glossary.violations`.

## Consequences

- Manual, LLM engine: `translated_names` 49 → 0 with rules 1 to 4 as
  first written. With the names of rules 4 (capitals) and 5 (quoted) now
  counted too: 35 → 2. Kept now: "Main Menu", "Network Setup", "IP
  Address", "View configuration", the ‘Control’ tab, the ‘Browse’ button,
  the “User” and “Password” prompts.
- The 2 left start a sentence with the name ("Network Setup is used
  to..."). Asked again with the name named, the 4B model copies the whole
  sentence in English, which is worse and rejected; rewording the request
  or asking to edit the Portuguese answer did not help. This is a limit of
  the model (ADR 0007), reported by the check.
- Kept terms match whole words: "IP Address" is not in "IP addresses". A
  substring match had listed it in prompts where it did not occur, and the
  extra "keep" lines pushed the model to copy whole sentences.
- The translation cache key includes the kept names.
- The pseudo engine also honors `kept_terms`, so the corpus measures the
  same policy.

## Refinements

- **2026-11-16:** names also written as ordinary text were translated
  everywhere; they are now kept with their capitals (rule 4). Quoted names
  of prompts, tabs, buttons and keys were translated in some steps and
  kept in others; they are now kept where quoted (rule 5). Decided by the
  user.
- **2026-11-17:** found by the translation review: three paragraphs (p15
  "To choose one of the menu choices...", p18 "Press W if...", p19 "At
  this point, the serial connector...") were left in English. The PDF
  holds each in a screen's block, and the whole block was kept. Only the
  lines in the screen's style are kept now (rule 3).
