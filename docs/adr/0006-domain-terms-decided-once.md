# ADR 0006: Domain terms are decided once per document

- **Status:** Accepted
- **Date:** 2026-11-16
- **Cause:** T5 of [the fidelity sweep](../architecture.md#phase-8-translation-fidelity-by-cause)

## Context

The document-wide glossary covers capitalized multi-word terms
(`terms.recurring_terms`). Single domain words are decided again at each
use, often in their everyday sense:

| Term | Renderings found |
|---|---|
| card | placa / cartão / módulo / "da cartão" |
| frame (chassis) | quadro / frame / estrutura |
| card edge | do cartão / placa de borda / lado da cartão / bordo da placa |
| jumper | RETIFOS / Pinos de Salto / jumper |
| crossover | Cabo de Cruzamento / crossover / cabo cruzado |
| straight-through | direto / reta / Conexão Direta / retificado |
| female | feminino / fêmea |
| full/half duplex | "duplo ou simples" |
| prompt | prompt / mensagem / solicitação / tela / janela |
| slot | slot / posição |
| header (pin header) | cabeçalho |
| netmask | máscara de rede / máscara de subrede |

The sweep found 24 inconsistent terms and 46 changes of meaning; most of
the meaning changes are these words. `inconsistent_words` measures part of
it (9 → 3 after ADRs 0002–0004).

## Decision

1. **Candidates by where the document uses them, not by case**
   (`terms.domain_words`): single words after a determiner in running
   text ("the card", "a jumper", "the 7700FC card"), or in short texts
   (cells, labels: "9 pin D Female"), in 3 texts or more. Not counted:
   - words the document only writes capitalized (names: "Ethernet");
   - participles and gerunds ("the selected", "the following");
   - words only seen describing another ("the front panel");
   - kept names and the user's glossary terms.
2. **At most 40 words**, those used in most texts (`MAX_DOMAIN_WORDS`).
   Multi-word capitalized terms keep their own list (`recurring_terms`).
3. **The model decides each word, with the document's context**:
   - it is given the document's description (now written before the
     terminology) and one sentence where the word names the thing ("Align
     the card with the card guide...");
   - it translates that sentence and says which word it used, as JSON, one
     word per request. Asked what a word is, the 4B model answered with
     its everyday sense or kept it ("card" → "cartão", "pin" → KEEP);
     translating a sentence, it writes the field's word ("placa", "pino").
     Given a list, it dropped lines and copied the answer's placeholders;
   - a word its translation does not contain is no decision;
   - the word kept as written is "keep".
4. **The decision goes into the batch prompts** like the rest of the
   glossary, and a translation that misses it is retried. A fixed word
   matches whole and in the plural ("port", "ports", not "support"), and
   not inside a kept name ("network" in "Network Setup").
5. **The user's glossary wins** (`--glossary terms.json`, which already
   existed): its terms are not asked about.
6. The fast engine has no model to decide; it is unchanged.
7. Check: `inconsistent_words` in `tools/defects.py`, as before.

## Consequences

- Manual, LLM engine, renderings of the words over the whole document:

  | Word | Before | After |
  |---|---|---|
  | card | placa 17, cartão 16, módulo 1 | placa 33, módulo 1 |
  | edge | borda 8, lado 2, other 8 | borda 18 |
  | end | extremidade 2, fim 2, other 3 | extremidade 5, other 2 |
  | port | porta 19, porto 3 | porta 23 |
  | frame | quadro 106, frame 6, estrutura 6 | quadro 114, frame 4 |
  | slot | slot 5, posição 4 | posição 8, slot 1 |
  | prompt | solicitação 7, prompt 4, other 7 | solicitação 15, prompt 2, mensagem 1 |

  "PC End" is now "Extremidade do PC". `inconsistent_words` 4 (color
  words of cells, "Baud", "LEDs"), `left_in_english` 4, `changed_tokens` 0, `translated_names` 3 (the
  "Network Setup is used..." case, ADR 0007).
- A wrong decision is now wrong everywhere: "gateway" → "porta de
  entrada" (15 of 15; Brazilian technicians say "gateway"), "revision" →
  "versão". The user's glossary corrects such a word for a document.
- Words the list leaves out are still decided at each use: "jumper" (7
  kept, 2 "RETIFOS"), "female", "crossover", "ribbon" ("Cabo de rabo"),
  "Baud" ("Velocidade"). They are used in fewer than 3 texts or below the
  40 most used.
- About 40 short requests more per document (about 1 minute on the manual).

## Revision: decisions checked against a second use (2026-11-23)

On the ISMUX-004 datasheet, single answers were wrong and imposed on the
whole document: "standard" -> "transmissão" (five "STANDARD" cells read
"TRANSMISSÃO"), "mode" -> "hierárquicos" ("Burst or continuous mode" ->
"Modo hierárquico"). The model reported a word of another part of the
sentence. And "Electric Linear", of "KOKUSAI DENKI Electric Linear S/A",
was translated as a term.

- A domain word is decided in two of its uses, and only when both
  answers agree (singular or plural, by Portuguese plural endings); when
  they differ ("porta", "porto"), a third use breaks the tie; otherwise
  each sentence decides. One or two requests more per word.
- A recurring term always beside an all-capital word or a company ending
  ("S/A", "Inc") is part of a name and kept (`terms.name_fragments`).
- The document's description no longer lists names to keep: the model
  listed ordinary words ("STANDARD", "Modulation"; "Jumpers" on the
  manual), and told to keep "STANDARD" while translating it, wrote
  Spanish ("ESTÁNDAR"). The names the document itself shows to be names
  (screen items, labels, quoted names; `terms.kept_terms`) are listed
  with the texts to translate instead, not when deciding the
  terminology: there they made the model keep "frame".

## Decisions taken

- **2026-11-18:** the user chose at most 40 words picked by use (not only
  words seen rendered two ways), decided by the model with the document's
  context (not a fixed pt-BR list). A user glossary file already existed
  (`--glossary`).
- **2026-11-23:** two agreeing uses per word and name pieces kept, chosen
  by the user.
