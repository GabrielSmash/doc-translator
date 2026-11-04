import argparse
import logging
import sys
from pathlib import Path

from .pipeline import translate_pdf
from .translators.opus import OpusTranslator

DEFAULT_MODEL_DIR = Path.home() / ".cache" / "doc-translator" / "models" / "opus-mt-en-pt"
PT_BR_TOKEN = ">>pob<<"


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="doc-translator",
        description="Translate a PDF from English to Brazilian Portuguese, keeping its layout.",
    )
    parser.add_argument("input", type=Path, help="PDF to translate")
    parser.add_argument("-o", "--output", type=Path, help="default: <input>.pt-BR.pdf")
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL_DIR, help="CTranslate2 model dir")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s: %(message)s"
    )

    if not args.input.is_file():
        sys.exit(f"error: {args.input} not found")
    if not (args.model / "model.bin").is_file():
        sys.exit(
            f"error: translation model not found in {args.model}\n"
            "Run `python scripts/convert_model.py` to create it."
        )

    output = args.output or args.input.with_suffix(".pt-BR.pdf")
    translator = OpusTranslator(args.model, PT_BR_TOKEN, args.device)
    translate_pdf(args.input, output, translator)
    print(f"Saved {output}")
