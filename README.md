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
uv tool install doc-translator              # or: pipx install doc-translator
doc-translator document.pdf                 # writes document.pt-BR.pdf
```

From the source:

```bash
git clone https://github.com/GabrielSmash/doc-translator
cd doc-translator
uv run doc-translator document.pdf
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
| `--report FILE` | write what the run changed (per page, fonts, warnings) as JSON |
| `--no-cache` | translate everything again instead of reusing earlier translations |
| `-v` | verbose logs (glossary, fonts used, text that had to shrink...) |

At the end of a run a summary states what changed: the share of the text
translated, which pages had paragraphs drawn smaller, which fonts were
replaced and how many warnings were logged. If a page cannot be processed it
is kept in the original language, listed in the summary, and the exit code
is 1.

Translations are cached in `~/.cache/doc-translator/translations.sqlite`
(per engine, model, glossary and version): running a document again does not
translate it again, and an interrupted `llm` run (Ctrl+C, Ollama down)
resumes where it stopped.

### LLM engine

Install [Ollama](https://ollama.com/download) and the model:

```bash
ollama pull qwen3:4b-instruct
doc-translator document.pdf --engine llm
```

Before translating, the model reads the document's headings and opening
text and writes a short description of it (field, audience, names that stay
as they are); every batch is sent with that description, the heading of its
section and the preceding paragraphs.

For better translations, at the cost of time, use a larger model:
`--llm-model qwen3:8b` (on a 4 GB GPU, part of it runs on the CPU: about
4–5 times slower).

Each translation is checked; anything that fails is retried on its own and,
if still wrong, reported in the logs (the lesser of the two problems is
kept):
- every number of the original must be in the translation (pt-BR decimal
  commas allowed), since small models occasionally alter them;
- implausible lengths (explanations, truncated answers, a heading answered
  with a sentence) are rejected;
- text coming back in English is retried;
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

Both engines also keep in English the names the document sets in italics and
always writes the same way, as manuals do with screens, menus and fields
("the *Network Setup* menu").

### GPU for the fast engine

```bash
uv tool install 'doc-translator[gpu]'   # with the CUDA libraries CTranslate2 needs
uv sync --extra gpu                     # the same, from the source
```

## How it works

```text
read     → text lines, drawings and links of each page (PyMuPDF)
analyze  → every line goes into one block with a kind (paragraph, heading,
           list item, table cell, TOC entry, caption, code, marker, symbols,
           rotated) and an action: translate, or keep as it is
translate→ fast: sentence by sentence; llm: glossary + batches in reading order
align    → bold/italic/links are mapped onto the translated text
layout   → where each translation goes: down to the letters of the next
           element, inside the colored area it sits on, up to 10% wider into
           free space; text of one style on a page shares one size; embedded
           fonts are reused when they contain every needed glyph
draw     → original text removed, translations written, links recreated
           over the translated words (internal links too)
```

## Known limitations

- Portuguese is longer than English: when there is no free space around a
  paragraph, its font is reduced to fit, and the text of the same style on
  that page with it (in 5% steps), so sizes stay uniform.
- Subset fonts (the most common case) cannot draw letters the original
  text didn't use, so a font with the same metrics (Times, Helvetica or
  Courier) is used instead.
- A translation that runs longer than the original line can reach the dot
  leaders of a form (". . . . ."), which stay in place.
- Formulas are flattened: sub and superscripts inside them are not kept.
- Monospaced text (code, terminal output) is not translated.
- Scanned PDFs (images without text) are not supported: run OCR on them first
  (e.g. [OCRmyPDF](https://ocrmypdf.readthedocs.io)). Password-protected PDFs
  are refused too.

## Development

```bash
uv run pytest                    # unit tests and synthetic fixtures (~2 s)
uv run pytest --corpus           # also the public corpus (downloads ~3.4 MB once)
uv run python -m tools.measure   # quality metrics per fixture and engine
uv run python -m tools.measure --corpus --doc my.pdf --engine identity fast
uv run python -m tools.defects --doc my.pdf --engine fast   # each defect, where it is
```

`tools/measure.py` runs the real pipeline and compares its output with the
original: text coverage, font reduction, characters left without ink, lost
drawings, links and rotated text, overlaps, layout similarity, file size.
Besides the real engines it has two that need no model: `identity` (original
text through the whole pipeline, so any difference is a rendering defect)
and `pseudo` (accented, ~10% longer text, like a translation).

`tools/defects.py` checks rules every translated page must keep and lists
each case with its page: lines read in pieces, text over other text, text
out of its table cell, misplaced table-of-contents numbers, lost alignment,
cells whose translation grew, cells taken for headings, short texts whose
form changed. A defect noticed by eye is first turned into a check (and a
fixture), so its whole class is counted and fixed, not the one example.

`tests/baseline.json` stores the current numbers; a test fails when a metric
gets worse. After an improvement, store the new numbers with
`uv run python -m tools.measure --fixtures --corpus --update-baseline`.
Known defects have tests marked `xfail(strict=True)`: when a fix makes one
pass, remove its marker. See [docs/architecture.md](docs/architecture.md).

Releases: set the version in `pyproject.toml`, then push a tag with the same
version (`git tag v0.2.0 && git push origin v0.2.0`). The `Publish` workflow
runs the tests and uploads the package to PyPI (trusted publishing).

## License

[AGPL-3.0-or-later](LICENSE). The `fast` engine's model is a conversion of
[Helsinki-NLP/opus-mt-tc-big-en-pt](https://huggingface.co/Helsinki-NLP/opus-mt-tc-big-en-pt),
licensed CC-BY-4.0.
