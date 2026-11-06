import argparse
import logging
import sys
from pathlib import Path

from huggingface_hub import snapshot_download
from huggingface_hub.errors import HfHubHTTPError, LocalEntryNotFoundError

from .pipeline import translate_pdf
from .translators.opus import OpusTranslator

# Conversion of Helsinki-NLP/opus-mt-tc-big-en-pt published by this project
# (see scripts/convert_model.py).
MODEL_REPO = "gabrielsmash/opus-mt-tc-big-en-pt-ct2-int8"
PT_BR_TOKEN = ">>pob<<"


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="doc-translator",
        description="Translate a PDF from English to Brazilian Portuguese, keeping its layout.",
    )
    parser.add_argument("input", type=Path, help="PDF to translate")
    parser.add_argument("-o", "--output", type=Path, help="default: <input>.pt-BR.pdf")
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    parser.add_argument(
        "--model", type=Path, help=f"local CTranslate2 model dir (default: download {MODEL_REPO})"
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s: %(message)s"
    )

    if not args.input.is_file():
        sys.exit(f"error: {args.input} not found")
    model_dir = args.model or _download_model()
    if not (model_dir / "model.bin").is_file():
        sys.exit(f"error: no CTranslate2 model (model.bin) in {model_dir}")

    output = args.output or args.input.with_suffix(".pt-BR.pdf")
    translator = OpusTranslator(model_dir, PT_BR_TOKEN, args.device)
    translate_pdf(args.input, output, translator)
    print(f"Saved {output}")


def _download_model() -> Path:
    """Fetch the model on first use; later runs reuse the Hugging Face cache."""
    try:
        return Path(snapshot_download(MODEL_REPO))
    except (HfHubHTTPError, LocalEntryNotFoundError) as error:
        sys.exit(f"error: could not download the model {MODEL_REPO}: {error}")
