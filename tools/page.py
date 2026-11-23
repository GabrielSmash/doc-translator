"""Translate one page of a document, to look at a reported error quickly.

The page is translated with what the whole document tells: the LLM engine
reads the document's description, terminology and vocabulary from every
page, as in a full run, and translates only this page's text. Writes, in
`--out` (default samples/tmp/, git-ignored):

- <name>.p<N>.pdf and <name>.p<N>.pt-BR.pdf: the page and its translation;
- <name>.p<N>.png and <name>.p<N>.pt-BR.png: both rendered;
- <name>.p<N>.txt: each text and its translation, then the defects found;
- <name>.p<N>.json: the translations, which `--reuse` writes again without
  the engine (to look at a layout change in seconds).

Usage:
    uv run python -m tools.page samples/7700.pdf 14
    uv run python -m tools.page samples/7700.pdf 5 --engine fast
    uv run python -m tools.page samples/7700.pdf 5 --reuse
"""

import argparse
import json
import logging
from pathlib import Path

import pymupdf

from doc_translator.pipeline import translate_document
from doc_translator.translators.base import Unit, UnitTranslation
from tools.defects import find
from tools.measure import make_engine

DPI = 110


class _Units:
    """An engine that only collects the document's units."""

    cache_id = "units"

    def translate_units(self, units: list[Unit]) -> list[UnitTranslation]:
        self.units = list(units)
        return [UnitTranslation(u.text, list(u.segments)) for u in units]


class _Replay:
    """An engine that returns translations saved by an earlier run."""

    cache_id = "replay"

    def __init__(self, saved: list[dict]) -> None:
        self.saved = {s["text"]: s for s in saved}

    def translate_units(self, units: list[Unit]) -> list[UnitTranslation]:
        results = []
        for unit in units:
            saved = self.saved.get(unit.text)
            results.append(
                UnitTranslation(saved["translation"], saved["segments"])
                if saved and len(saved["segments"]) == len(unit.segments)
                else UnitTranslation(unit.text, list(unit.segments))
            )
        return results


def _with_document_context(engine, document: list[Unit]):
    """The LLM engine, deciding its context from `document` instead of the
    units it translates."""
    from doc_translator.translators import llm

    class PageLLM(llm.LLMTranslator):
        def translate_units(self, units):
            kept_terms, vocabulary = llm.kept_terms, llm.vocabulary
            llm.kept_terms = lambda _units, *args: kept_terms(document, *args)
            llm.vocabulary = lambda _units, *args: vocabulary(document, *args)
            try:
                return super().translate_units(units)
            finally:
                llm.kept_terms, llm.vocabulary = kept_terms, vocabulary

        def _describe(self, units):
            return super()._describe(document)

        def _build_glossary(self, units, kept=None):
            return super()._build_glossary(document, kept)

    engine.__class__ = PageLLM
    return engine


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("page", type=int, help="page number, from 1")
    parser.add_argument("--engine", default="llm", choices=["llm", "fast", "identity", "pseudo"])
    parser.add_argument("--out", type=Path, default=Path("samples/tmp"))
    parser.add_argument("--reuse", action="store_true", help="the translations of the last run")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    stem = args.out / f"{args.pdf.stem}.p{args.page}"

    if args.reuse:
        engine = _Replay(json.loads(Path(f"{stem}.json").read_text()))
    else:
        engine = make_engine(args.engine)
        if args.engine == "llm":
            collector = _Units()
            translate_document(pymupdf.open(args.pdf), collector)
            engine = _with_document_context(engine, collector.units)

    index = args.page - 1
    page = pymupdf.open()
    page.insert_pdf(pymupdf.open(args.pdf), from_page=index, to_page=index)
    original = pymupdf.open("pdf", page.tobytes())
    result = translate_document(page, engine)

    args.out.mkdir(parents=True, exist_ok=True)
    if not args.reuse:
        saved = [
            {"text": u.text, "translation": t.text, "segments": t.segments}
            for u, t in zip(result.units, result.translations)
        ]
        Path(f"{stem}.json").write_text(json.dumps(saved, ensure_ascii=False, indent=1))
    original.save(f"{stem}.pdf")
    page.save(f"{stem}.pt-BR.pdf")
    original[0].get_pixmap(dpi=DPI).save(f"{stem}.png")
    page[0].get_pixmap(dpi=DPI).save(f"{stem}.pt-BR.png")

    lines = [f"{u.text!r} -> {t.text!r}" for u, t in zip(result.units, result.translations)]
    findings = find(original, page, result)
    lines += ["", f"{len(findings)} defects:"] + [str(f) for f in findings]
    Path(f"{stem}.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nWritten to {stem}.*")


if __name__ == "__main__":
    main()
