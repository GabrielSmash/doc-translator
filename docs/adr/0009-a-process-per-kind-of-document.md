# ADR 0009: A process per kind of document

- **Status:** Accepted
- **Date:** 2026-11-24

## Context

ADR 0008 added comics to the same run as documents: `translate_pdf`
translated the text pages, then the image pages, with the same engine and
model. A comic needs other choices than a manual:

- **Context:** balloons are short and a scene runs over pages. With the
  three preceding balloons as context, the 4b model wrote "pernas" for
  Doctor Octopus's arms.
- **Model:** on the whole comic (25 pages, 240 balloons):

  | Run | Time | Translations reported |
  |---|---|---|
  | 4b, 3 balloons before | 9 min | 9 |
  | 4b, ~2 pages before and 6 after | 17 min | 14 |
  | 8b, ~2 pages before and 6 after | 40 min | 5 |

  The 8b model wrote "braços", "ELES SÃO INVEJOSOS DE MIM!", "TESTES
  EXAUSTIVOS" where the 4b one wrote "pernas", "THEY ESTÃO ME ODIANDO!",
  "TESTES EXHAUSTIVE". More context alone helped the 4b model in some
  balloons and confused it in others. On the reference manual, the 8b model
  was not better (ADR 0007): the choice depends on the kind of document.
- **What the user knows:** who the characters are, what the story is; the
  model only guesses it from the text.

## Decision

1. **A general flow and a process per kind of document.**
   - `pipeline.py` is the flow every document goes through: open, choose
     its process, translate, save.
   - `result.py` is what a run produced, read by the report.
   - Each process is in `modes/`:
     - `document.py`: text PDFs, layout kept;
     - `comic.py`: page images, OCR and lettering, built on `images/`.
   - The engines, glossary, cache, checks and report are shared.
   - A process has its own defaults: engine and Ollama model.
2. **Choosing the process:** `--mode auto|document|comic`.
   - `auto`: a PDF with text is a document; one with no text and page
     images is a comic.
   - A document's image pages are left as they are, with a warning. Lettering
     a scanned manual page as a comic would be wrong; scanned documents
     will be a process of their own.
3. **Comic defaults:** the LLM engine with `qwen3:8b`. Each batch sees the
   30 balloons before it and the 6 after it (`STORY_CONTEXT`,
   `STORY_FOLLOWING` in the LLM engine, used when the units are balloons).
4. **Context from the user:** `--context TEXT`, for the LLM engine, in any
   process.
   - The model reads it before describing the document.
   - It is shown first in the description sent with every prompt, so the
     terminology and name decisions see it too.
   - It is part of the cache key.

## Decisions taken

- **2026-11-24:** by the user:
  - more context for balloons and the 8b model for comics;
  - a general pipeline with an isolated process per kind of document;
  - an initial context given by the user.
