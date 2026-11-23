# ADR 0007: Limits of the local model

- **Status:** Accepted
- **Date:** 2026-11-16
- **Cause:** T6 of [the fidelity sweep](../architecture.md#phase-8-translation-fidelity-by-cause)

## Context

After ADRs 0002–0006, what remains are errors of the language itself:

| Kind | Found | Examples |
|---|---|---|
| Grammar | 27 | "da cartão", "MONITADAS", "repositione", "reinsertado", "as LEDs" |
| Invented or wrong words | part of 46 | "Guidões" (handlebars) for guidelines, "Concerna", "RETIFOS", "parede LED" for LED pair |
| Wrong part of speech | several | "Type:" (verb) → "Tipo:" (noun), where "Digite:" was correct elsewhere |
| Heading style | 16 | "SUBSTITUIR A BATERIA" beside "INSTALAÇÃO DA..." and "CONFIGURANDO" |

These come from the model: Ollama `qwen3:4b-instruct`, chosen to run on a
laptop. No check or context fixes a word the model does not know. The
document gives it enough context in the cases above.

## Options

| Option | Gain | Cost |
|---|---|---|
| A. Larger default model (8B–14B) | Fewer grammar and vocabulary errors | 2–4× time and memory; some users' machines can't run it |
| B. Model as a setting, 4B by default | The user chooses quality or speed | Results vary per machine; documentation |
| C. Review pass: a second call that reads source and translation and fixes only errors | Catches agreement and invented words | ~2× LLM time; risk of the reviewer changing correct text |
| D. Target-language checks: unknown words against a pt-BR word list | Cheap; finds "RETIFOS", "MONITADAS" | Needs a word list (license); technical words give false alarms |

## Measured: a larger model (option A)

2026-11-18, the manual, the same code, a GTX 1650 Ti with 4 GB:

| | `qwen3:4b-instruct` | `qwen3:8b` |
|---|---|---|
| Time | 13 min | 60 min (does not fit the GPU) |
| Same translation | 298 units | |
| Differing units without a problem (blind A/B review, 285 units) | 224 | 207 |
| Better of the two | 69 | 48 (tie 168) |
| `left_in_english` | 4 | 11 ("Figure", "edge" kept) |
| `translated_names` / `inconsistent_words` | 5 / 3 | 3 / 3 |

The 8B model is not better here: it keeps more words in English and
makes as many grammar errors, at 4.5× the time. The prompts were tuned on
the 4B model, which may favor it; still, a larger default is not
justified on this machine. Option B already exists: `--llm-model`.

## Decision

Option D, with B as it is (`--llm-model`, 4B by default).

1. **Words of a translation are looked up in a dictionary of the target
   language** (`spelling.py`): LibreOffice's VERO pt-BR Hunspell
   dictionary (LGPLv3 or MPL 2.0), read by spylls (MIT, pure Python). The
   dictionary is downloaded on first use from a pinned commit, checked
   against its SHA-256, and kept in `~/.cache/doc-translator/dictionaries`.
   Without it (no network), words are not checked.
2. **Not counted as invented:**
   - words of the source text or of the whole document ("jumpers",
     "broadcast"), and their parts ("pré-softmax");
   - all-capital abbreviations of up to 4 letters;
   - capitalized words inside a text: names ("François");
   - compounds made of words ("auto-atenção", "multi-cabeça").
3. **The LLM engine retries** a translation with an invented word
   (severity 2, like a changed number); if the word stays, the last try
   names it ("A previous translation used words that do not exist in
   Brazilian Portuguese: ...").
4. **A glossary decision with an invented word is dropped** (ADR 0006):
   "netmask" → "máscara de subrede" would put "subrede" everywhere.
5. Check: `unknown_words` in `tools/defects.py` and the corpus measure.

The Debian word list (`wbrazilian`) was rejected: GPL-2 only, which
AGPL-3.0 code cannot use, and it lacks forms like "reinserido".

## Consequences

- Manual, LLM engine: texts with invented words 24 → 10. Fixed:
  "RETIFOS" (JUMPERS), "Comply", "cometiu", "ejeitor", "ejeutor", the
  glossary's "subrede" in the Netmask cells. Left: "defaulta",
  "repositione", "reinsertado", "subrede" (in sentences) — the 4B model
  writes them again even when named — and "antistática", a false alarm.
- Time: 13 → 14.5 min.
- False alarms on the corpus with the fast engine: arXiv 5 ("hiperparâmetros",
  "autoatenção", "logaritmicamente": real words missing from the
  dictionary), NIST 2, the manual 1 ("submontagem"). They cost a retry,
  and the retry is kept only when it has fewer problems.
- Not covered: agreement ("da cartão", "à adaptador"), wrong sense, wrong
  part of speech. They need a review pass (option C), left for later.

## Revision: invented words from source words; texts in capitals (2026-11-23)

- Named in the last try, an invented word was written again, and naming
  it could bring it in: "prompção" came back every time it was named,
  "mensagem" or "prompt" when it was not. An invented word still there
  that is a source word made to look Portuguese ("prompção" from
  "prompt", "defaulta" from "default", "reinsertado" from "reinserted":
  5 letters or more alike from the start) is written as the source word
  (`Spelling.source_words_for`). Other invented words ("subrede") stay
  and are reported.
- Texts in capitals are asked in sentence case, abbreviations and names
  kept ("DHCP", "EVERTZ"), and answered in capitals: in capitals the
  model returned "UPGRADING MODULE FIRMWARE THROUGH THE 7700FC FRAME
  CONTROLLER" as written or wrote "SISTEMA DE MÓDULOS ... CONTROLLER DE
  FRAME".
- Manual, LLM engine: invented words 15 -> 7; the heading translated.

## Revision: titles in sentence case (2026-11-23)

Heading and label style varied run by run and within one diagram ("Status
do quadro" beside "Comunicação de Quadro"), and an uppercase answer to a
title-case source was rewritten in title case. Titles and labels are now
written in sentence case, as Brazilian manuals do (`llm._match_case`):

- a capitalized word of a title-case source's translation is lowercased
  when it was translated (not a word of the source), the pt-BR dictionary
  has it in lowercase, and the words beside it were translated too
  ("Comunicação de Quadro" -> "Comunicação de quadro");
- words of the source keep its spelling: names and codes left as they
  are ("Paula Cunha", "Single Frequency Network", "STD-B31", "Base-T");
  a translated word beside them belongs to the name ("MG – Brasil –
  CEP"); a caption starts again after its colon ("Figura 1: Diagrama de
  blocos");
- sentences are left as the model writes them.

## Decisions taken

- **2026-11-18:** a larger default model was measured (above) and
  rejected by the user's choice of option D.
- **2026-11-19:** option D, with the VERO dictionary.
- **2026-11-23:** titles and labels in sentence case, by the user.
