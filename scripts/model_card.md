---
license: cc-by-4.0
language:
- en
- pt
base_model: Helsinki-NLP/opus-mt-tc-big-en-pt
library_name: ctranslate2
pipeline_tag: translation
tags:
- ctranslate2
- opus-mt
- int8
---

# opus-mt-tc-big-en-pt (CTranslate2, int8)

English → Portuguese translation model used by
[doc-translator](https://github.com/GabrielSmash/doc-translator), a free and
open source CLI that translates PDFs while keeping their layout.

This is an **unmodified conversion** of
[Helsinki-NLP/opus-mt-tc-big-en-pt](https://huggingface.co/Helsinki-NLP/opus-mt-tc-big-en-pt)
to the [CTranslate2](https://github.com/OpenNMT/CTranslate2) format with int8
weight quantization, so it runs fast on CPU (and on CUDA GPUs) without
PyTorch. The SentencePiece tokenizers (`source.spm`, `target.spm`) are
included. No weights were retrained or fine-tuned.

## Attribution

The original model was created by the
[OPUS-MT project](https://github.com/Helsinki-NLP/Opus-MT) (Language Technology
Research Group, University of Helsinki) and is licensed under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). This conversion is
distributed under the same license. The upstream model card is kept as
`UPSTREAM_README.md`.

> Tiedemann, J. and Thottingal, S. (2020). OPUS-MT — Building open translation
> services for the World. *Proceedings of EAMT 2020*.

## Usage

The target language token is required: `>>pob<<` for Brazilian Portuguese,
`>>por<<` for European Portuguese.

```python
import ctranslate2, sentencepiece as spm

translator = ctranslate2.Translator(MODEL_DIR, device="cpu", compute_type="int8")
src = spm.SentencePieceProcessor(f"{MODEL_DIR}/source.spm")
tgt = spm.SentencePieceProcessor(f"{MODEL_DIR}/target.spm")

tokens = [">>pob<<", *src.encode("Hello, world!", out_type=str), "</s>"]
result = translator.translate_batch([tokens], beam_size=4)
print(tgt.decode(result[0].hypotheses[0]))
```

## Reproducing

Produced by
[`scripts/convert_model.py`](https://github.com/GabrielSmash/doc-translator/blob/main/scripts/convert_model.py)
with CTranslate2 {ct2_version}.
