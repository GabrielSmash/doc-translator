# ADR 0006: Domain terms are decided once per document

- **Status:** Proposed
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

## Proposal

1. **Candidates by frequency and spread, not by case:** words and short
   phrases (1–2 words) that recur in prose on several pages and are not
   stopwords.
2. **One decision per candidate**, made by the LLM with two or three
   example sentences of the document, in the same pass as the existing
   glossary. A decision is either a rendering or "keep as written".
3. **The decision goes into every batch prompt**, like today's glossary,
   and into the fast engine as kept terms when the decision is "keep".
4. Check: `inconsistent_words` extended to the glossary's terms. A term
   rendered two ways counts as one defect.

## To decide

1. **How many terms:** the prompt grows with each one. A cap (today 40 for
   multi-word terms), or only terms seen with two renderings in a first
   pass.
2. **Who decides "keep" vs "translate"** for borrowed words (`jumper`,
   `slot`, `crossover`, `prompt`): the model with context, or a
   pt-BR list in `languages.py` that the user can edit.
3. **A user glossary file** (`--glossary terms.csv`) that overrides the
   model's decisions: useful for a company's own terminology. It adds a
   CLI option.
