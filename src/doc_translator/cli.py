import argparse
import logging
import sys
from pathlib import Path

from huggingface_hub import snapshot_download
from huggingface_hub.errors import HfHubHTTPError, LocalEntryNotFoundError

from .pipeline import translate_pdf
from .translators.base import Glossary
from .translators.llm import DEFAULT_HOST, LLMError, LLMTranslator
from .translators.opus import OpusTranslator

# Conversion of Helsinki-NLP/opus-mt-tc-big-en-pt published by this project
# (see scripts/convert_model.py).
MODEL_REPO = "gabrielsmash/opus-mt-tc-big-en-pt-ct2-int8"
PT_BR_TOKEN = ">>pob<<"
DEFAULT_LLM = "qwen3:4b-instruct"


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="doc-translator",
        description="Translate a PDF from English to Brazilian Portuguese, keeping its layout.",
    )
    parser.add_argument("input", type=Path, help="PDF to translate")
    parser.add_argument("-o", "--output", type=Path, help="default: <input>.pt-BR.pdf")
    parser.add_argument(
        "--engine",
        choices=["fast", "llm"],
        default="fast",
        help="fast: Opus-MT, sentence by sentence; llm: local LLM via Ollama, "
        "context-aware and slower",
    )
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    parser.add_argument("--llm-model", default=DEFAULT_LLM, help="Ollama model (llm engine)")
    parser.add_argument("--ollama-host", default=DEFAULT_HOST)
    parser.add_argument(
        "--glossary",
        type=Path,
        help='JSON file: {"keep": ["terms to keep"], "terms": {"source": "translation"}}',
    )
    parser.add_argument(
        "--model", type=Path, help=f"local CTranslate2 model dir (default: download {MODEL_REPO})"
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    # Verbose output is about this tool only, not the HTTP/model libraries.
    logging.getLogger("doc_translator").setLevel(logging.DEBUG if args.verbose else logging.INFO)

    if not args.input.is_file():
        sys.exit(f"error: {args.input} not found")
    output = args.output or args.input.with_suffix(".pt-BR.pdf")
    glossary = None
    if args.glossary:
        try:
            glossary = Glossary.load(args.glossary)
        except (OSError, ValueError) as error:
            sys.exit(f"error: invalid glossary {args.glossary}: {error}")
    if args.engine == "llm":
        try:
            translator = LLMTranslator(args.llm_model, args.ollama_host, glossary)
        except LLMError as error:
            sys.exit(f"error: {error}")
    else:
        model_dir = args.model or _download_model()
        if not (model_dir / "model.bin").is_file():
            sys.exit(f"error: no CTranslate2 model (model.bin) in {model_dir}")
        translator = OpusTranslator(model_dir, PT_BR_TOKEN, args.device, glossary)
    translate_pdf(args.input, output, translator)
    print(f"Saved {output}")


def _download_model() -> Path:
    """Fetch the model on first use; later runs reuse the Hugging Face cache."""
    try:
        return Path(snapshot_download(MODEL_REPO))
    except (HfHubHTTPError, LocalEntryNotFoundError) as error:
        sys.exit(f"error: could not download the model {MODEL_REPO}: {error}")
