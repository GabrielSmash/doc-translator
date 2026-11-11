# doc-translator

Free and open source CLI that translates PDF documents **while keeping their
layout**: same positions, font sizes, colors, bold/italic inside sentences,
links, underlines, tables and tables of contents. Only the language changes.

Translation runs **locally**, with no paid API and no data leaving your
machine. Two engines:

| Engine | How | Speed (29-page manual, 4 GB GPU) | Quality |
|---|---|---|---|
| `fast` (default) | [Opus-MT](https://github.com/Helsinki-NLP/Opus-MT) on [CTranslate2](https://github.com/OpenNMT/CTranslate2), sentence by sentence | ~40 s | literal; misses domain terms |
| `llm` | local LLM via [Ollama](https://ollama.com) (`qwen3:4b-instruct`), with an automatic glossary and surrounding context | ~9 min | context-aware, consistent terminology |

Currently supported: **English → Brazilian Portuguese**.

## Usage

```bash
git clone https://github.com/GabrielSmash/doc-translator
cd doc-translator
uv run doc-translator document.pdf          # writes document.pt-BR.pdf
```

On the first run of the `fast` engine, its model (~230 MB) is downloaded from
[Hugging Face](https://huggingface.co/gabrielsmash/opus-mt-tc-big-en-pt-ct2-int8)
and cached.

| Option | Description |
|---|---|
| `-o FILE` | output path |
| `--engine fast\|llm` | translation engine (see above) |
| `--glossary FILE` | your terminology, for both engines (see below) |
| `--device auto\|cpu\|cuda` | `fast` engine: `auto` uses the GPU when available |
| `--model DIR` | `fast` engine: local CTranslate2 model instead of downloading one |
| `--llm-model NAME` | `llm` engine: Ollama model (default `qwen3:4b-instruct`) |
| `--ollama-host URL` | `llm` engine: Ollama server (default `http://localhost:11434`) |
| `-v` | verbose logs (glossary, fonts used, text that had to shrink...) |

### LLM engine

Install [Ollama](https://ollama.com/download) and the model:

```bash
ollama pull qwen3:4b-instruct
uv run doc-translator document.pdf --engine llm
```

Each translation is checked; anything that fails is retried on its own and,
if still wrong, reported in the logs:
- every number of the original must be in the translation (pt-BR decimal
  commas allowed), since small models occasionally alter them;
- implausible lengths (explanations, truncated answers) are rejected;
- casing copied from neighboring items is undone.

### Glossary

```json
{
  "keep": ["Community Strings", "VistaLINK"],
  "terms": {"Upgrade Port": "Porta de atualização"}
}
```

`keep` terms stay in English; `terms` always get the given translation. With
the `llm` engine your glossary is merged with the one built automatically.

### GPU for the fast engine

```bash
uv sync --extra gpu    # installs the CUDA libraries CTranslate2 needs
```

## How it works

```text
extract  → paragraphs with per-run styles, links, underlines, small caps,
           table cells, TOC entries (PyMuPDF); code and symbols left untouched
translate→ fast: sentence by sentence; llm: glossary + batches in reading order
align    → bold/italic/links are mapped onto the translated text
render   → original text removed, translation written in the same area,
           reusing the embedded fonts when they contain every needed glyph
```

## Known limitations

- Portuguese is longer than English: when there is no free space around a
  paragraph, its font is reduced to fit.
- Subset fonts (the most common case) cannot draw letters the original
  text didn't use, so a font with the same metrics (Times, Helvetica or
  Courier) is used instead.
- Rows of tightly packed tables are drawn slightly smaller than the original.
- Monospaced text (code, terminal output) is not translated.
- Scanned PDFs (images without text) are not supported.

## Development

```bash
uv run pytest                    # unit tests and synthetic fixtures (~2 s)
uv run pytest --corpus           # also the public corpus (downloads ~3.4 MB once)
uv run python -m tools.measure   # quality metrics per fixture and engine
uv run python -m tools.measure --corpus --doc my.pdf --engine identity fast
```

`tools/measure.py` runs the real pipeline and compares its output with the
original: text coverage, font reduction, characters left without ink, lost
drawings, links and rotated text, overlaps, layout similarity, file size.
Besides the real engines it has two that need no model: `identity` (original
text through the whole pipeline, so any difference is a rendering defect)
and `pseudo` (accented, ~10% longer text, like a translation).

`tests/baseline.json` stores the current numbers; a test fails when a metric
gets worse. After an improvement, store the new numbers with
`uv run python -m tools.measure --fixtures --corpus --update-baseline`.
Known defects have tests marked `xfail(strict=True)`: when a fix makes one
pass, remove its marker. See [docs/architecture.md](docs/architecture.md).

## License

[AGPL-3.0-or-later](LICENSE). The `fast` engine's model is a conversion of
[Helsinki-NLP/opus-mt-tc-big-en-pt](https://huggingface.co/Helsinki-NLP/opus-mt-tc-big-en-pt),
licensed CC-BY-4.0.
