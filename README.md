# doc-translator

Free and open source CLI that translates PDF documents **while keeping their
layout**: same positions, font sizes, colors, bold/italic inside sentences,
links and underlines. Only the language changes.

Translation runs **locally**, with no paid API and no data leaving your
machine: an [Opus-MT](https://github.com/Helsinki-NLP/Opus-MT) model on
[CTranslate2](https://github.com/OpenNMT/CTranslate2), on the CPU or,
optionally, on an NVIDIA GPU.

Currently supported: **English → Brazilian Portuguese**.

## Usage

```bash
git clone https://github.com/GabrielSmash/doc-translator
cd doc-translator
uv run doc-translator document.pdf          # writes document.pt-BR.pdf
```

On the first run the translation model (~230 MB) is downloaded from
[Hugging Face](https://huggingface.co/gabrielsmash/opus-mt-tc-big-en-pt-ct2-int8)
and cached.

| Option | Description |
|---|---|
| `-o FILE` | output path |
| `--device auto\|cpu\|cuda` | `auto` uses the GPU when available, else the CPU |
| `--model DIR` | use a local CTranslate2 model instead of downloading one |
| `-v` | verbose logs (fonts used, text that had to shrink...) |

### GPU

```bash
uv sync --extra gpu    # installs the CUDA libraries CTranslate2 needs
```

## How it works

```text
extract  → paragraphs with per-run styles, links and underlines (PyMuPDF)
translate→ sentence by sentence, URLs and emails protected (Opus-MT)
align    → bold/italic/links are mapped onto the translated sentence
render   → original text removed, translation written in the same area,
           reusing the embedded fonts when they contain every needed glyph
```

## Known limitations

- Subset fonts (the most common case) cannot draw characters the original
  text didn't use, so a similar generic font is used instead.
- Centered or right-aligned text is laid out as left-aligned.
- Scanned PDFs (images without text) are not supported.

## License

[AGPL-3.0-or-later](LICENSE). The translation model is a conversion of
[Helsinki-NLP/opus-mt-tc-big-en-pt](https://huggingface.co/Helsinki-NLP/opus-mt-tc-big-en-pt),
licensed CC-BY-4.0.
