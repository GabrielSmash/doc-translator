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
3. **Screen blocks are kept as CODE**: not translated.
4. **The names a screen shows are kept everywhere** (`terms.screen_names`,
   through `Unit.names` into `kept_terms`), like the italic interface
   labels, but only when the document always writes them the same way.
5. Check: `translated_names` in `tools/defects.py`.

## Consequences

- Manual, LLM engine: `translated_names` 49 → 0.
- The translation cache key includes the kept names.
- The pseudo engine also honors `kept_terms`, so the corpus measures the
  same policy.

## Open points

1. **Names also written as ordinary text.** "Network Setup" and "Main Menu"
   are still translated, because the document also writes "the main menu"
   in lowercase, as plain text. Rule 4 then cannot tell the name from the
   phrase. Keeping only the capitalized form needs a case-sensitive keep in
   both engines (the glossary today matches without case).
2. **Quoted prompts in prose** ("User", "Password" at the ftp prompt) were
   translated in one step and kept in another. They are quoted, not shown
   in a screen block, so rule 4 doesn't see them.
