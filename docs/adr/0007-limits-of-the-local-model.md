# ADR 0007: Limits of the local model

- **Status:** Proposed
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

## To decide

1. **Which option, or which combination.** B with D is the cheapest; C is
   the most thorough.
2. **If A or B:** which models to measure, and on which machine as a
   reference.
3. **If D:** which word list (license compatible with AGPL-3.0).

This ADR comes last: the measure of what the model gets wrong is only
clean after ADRs 0005 and 0006 remove what the pipeline gets wrong.
