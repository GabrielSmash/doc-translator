"""Convert the official Opus-MT EN->PT model to CTranslate2 (int8).

This is how the model published by the project is produced. It needs the
heavy conversion dependencies, which end users never install:

    uv run --with "transformers<5" --with torch --with sacremoses \
        --index https://download.pytorch.org/whl/cpu --index-strategy unsafe-best-match \
        python scripts/convert_model.py [OUTPUT_DIR]
"""

import shutil
import sys
from pathlib import Path

import ctranslate2
from ctranslate2.converters import TransformersConverter
from huggingface_hub import hf_hub_download

SOURCE_MODEL = "Helsinki-NLP/opus-mt-tc-big-en-pt"
DEFAULT_OUTPUT = Path("build") / "opus-mt-tc-big-en-pt-ct2-int8"
MODEL_CARD = Path(__file__).with_name("model_card.md")

output = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT
TransformersConverter(SOURCE_MODEL).convert(str(output), quantization="int8", force=True)
# Tokenizers travel with the model so the runtime needs no transformers.
for name in ("source.spm", "target.spm"):
    shutil.copy(hf_hub_download(SOURCE_MODEL, name), output / name)
shutil.copy(hf_hub_download(SOURCE_MODEL, "README.md"), output / "UPSTREAM_README.md")
card = MODEL_CARD.read_text().replace("{ct2_version}", ctranslate2.__version__)
(output / "README.md").write_text(card)
print(f"Model written to {output}")
