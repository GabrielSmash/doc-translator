import argparse
import logging
import sys
from pathlib import Path

from huggingface_hub import snapshot_download
from huggingface_hub.errors import HfHubHTTPError, LocalEntryNotFoundError

from .languages import EN_PT_BR
from .pipeline import DocumentError, translate_pdf
from .report import Report
from .translators.base import Glossary
from .translators.cache import CachedTranslator, TranslationCache, default_path
from .translators.llm import DEFAULT_HOST, LLMError, LLMTranslator
from .translators.opus import OpusTranslator

LANGUAGES = EN_PT_BR
DEFAULT_LLM = "qwen3:4b-instruct"


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="doc-translator",
        description=f"Translate a PDF from {LANGUAGES.source} to {LANGUAGES.target}, keeping its layout.",
    )
    parser.add_argument("input", type=Path, help="PDF to translate")
    parser.add_argument("-o", "--output", type=Path, help=f"default: <input>.{LANGUAGES.target_code}.pdf")
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
        "--model",
        type=Path,
        help=f"local CTranslate2 model dir (default: download {LANGUAGES.opus_model})",
    )
    parser.add_argument("--report", type=Path, metavar="FILE", help="write what the run changed, as JSON")
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help=f"translate everything again instead of reusing earlier translations ({default_path()})",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    # Verbose output is about this tool only, not the HTTP/model libraries.
    logging.getLogger("doc_translator").setLevel(logging.DEBUG if args.verbose else logging.INFO)

    if not args.input.is_file():
        sys.exit(f"error: {args.input} not found")
    output = args.output or args.input.with_suffix(f".{LANGUAGES.target_code}.pdf")
    glossary = None
    if args.glossary:
        try:
            glossary = Glossary.load(args.glossary)
        except (OSError, ValueError) as error:
            sys.exit(f"error: invalid glossary {args.glossary}: {error}")
    if args.engine == "llm":
        try:
            translator = LLMTranslator(args.llm_model, args.ollama_host, glossary, LANGUAGES)
        except LLMError as error:
            sys.exit(f"error: {error}")
    else:
        model_dir = args.model or _download_model()
        if not (model_dir / "model.bin").is_file():
            sys.exit(f"error: no CTranslate2 model (model.bin) in {model_dir}")
        translator = OpusTranslator(model_dir, args.device, glossary, LANGUAGES)
    if not args.no_cache:
        translator = CachedTranslator(translator, TranslationCache(default_path()))
    try:
        result = translate_pdf(args.input, output, translator)
    except (LLMError, KeyboardInterrupt) as error:
        reason = "interrupted" if isinstance(error, KeyboardInterrupt) else f"error: {error}"
        resume = "" if args.no_cache else "\nParagraphs already translated were saved; run again to resume."
        sys.exit(f"{reason}{resume}")
    except DocumentError as error:
        sys.exit(f"error: {args.input}: {error}")

    report = Report.from_result(result, args.input, output)
    print(f"Saved {output}")
    print(report.summary())
    if args.report:
        report.write(args.report)
    if report.failed_pages:
        sys.exit(1)


def _download_model() -> Path:
    """Fetch the model on first use; later runs reuse the Hugging Face cache."""
    try:
        return Path(snapshot_download(LANGUAGES.opus_model))
    except (HfHubHTTPError, LocalEntryNotFoundError) as error:
        sys.exit(f"error: could not download the model {LANGUAGES.opus_model}: {error}")
