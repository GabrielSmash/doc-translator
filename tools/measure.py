"""Measure the pipeline: stage times and quality metrics, per document.

Runs the real pipeline (doc_translator.pipeline.translate_document) and
compares its output with the original. Engines:

- identity: original text through the whole pipeline, untranslated. Any
  difference from the input is a defect of extraction or rendering.
- pseudo: deterministic pseudo-localization (accented vowels, ~10% longer
  text). Exercises fonts and fitting like a translation, with no model.
- fast, llm: the real engines.

Usage:
    uv run python -m tools.measure                          # fixtures, identity + pseudo
    uv run python -m tools.measure --corpus --engine identity fast
    uv run python -m tools.measure --doc samples/7700.pdf --engine fast
    uv run python -m tools.measure --corpus --update-baseline
"""

import argparse
import json
import re
import statistics
import sys
import tempfile
import zlib
from pathlib import Path

import numpy as np
import pymupdf

from doc_translator.align import base_style, paragraph_runs
from doc_translator.analysis import Action
from doc_translator.pdf.read import TEXT_FLAGS, link_key
from doc_translator.pipeline import save, translate_document
from doc_translator.translators import Unit, UnitTranslation

from . import corpus, fixtures

BASELINE = Path(__file__).resolve().parent.parent / "tests" / "baseline.json"
DETERMINISTIC_ENGINES = ("identity", "pseudo")

# Direction of each metric, used to tell a regression from an improvement.
HIGHER_IS_BETTER = {
    "translated_pct",
    "full_size_pct",
    "styles_carried_pct",
    "original_font_chars_pct",
    "searchable_pct",
    "layout_row_corr_mean",
    "layout_row_corr_min",
    "layout_col_corr_mean",
}
LOWER_IS_BETTER = {
    "scaled_below_90_pct",
    "below_6pt",
    "not_fitted",
    "drawings_lost",
    "images_lost",
    "uri_links_lost",
    "internal_links_lost",
    "rotated_lines_lost",
    "kept_lines_lost",
    "blank_chars",
    "overlaps_added",
    "size_ratio",
    "numbers_lost_pct",
}


# -- engines -----------------------------------------------------------------


class Identity:
    def translate_units(self, units: list[Unit]) -> list[UnitTranslation]:
        return [UnitTranslation(u.text, list(u.segments)) for u in units]


class Pseudo:
    """Pseudo-localization: same words with accents, ~10% longer.

    Each word is transformed on its own, so a styled phrase transforms to
    exactly the matching part of its paragraph.
    """

    ACCENTS = str.maketrans("aeiouAEIOUcC", "áéíóúÁÉÍÓÚçÇ")
    WORD = re.compile(r"[A-Za-z]+")

    def translate_units(self, units: list[Unit]) -> list[UnitTranslation]:
        return [UnitTranslation(self._text(u.text), [self._text(s) for s in u.segments]) for u in units]

    def _text(self, text: str) -> str:
        return self.WORD.sub(self._word, text)

    def _word(self, match: re.Match) -> str:
        word = match.group(0)
        out = word.translate(self.ACCENTS)
        if zlib.crc32(word.encode()) % 3 == 0:
            out += "ão"
        return out


def make_engine(name: str):
    if name == "identity":
        return Identity()
    if name == "pseudo":
        return Pseudo()
    if name == "fast":
        from huggingface_hub import snapshot_download

        from doc_translator.languages import EN_PT_BR
        from doc_translator.translators.opus import OpusTranslator

        return OpusTranslator(Path(snapshot_download(EN_PT_BR.opus_model)))
    if name == "llm":
        from doc_translator.cli import DEFAULT_LLM
        from doc_translator.translators.llm import LLMTranslator

        return LLMTranslator(DEFAULT_LLM)
    raise ValueError(f"unknown engine {name!r}")


# -- measurement ---------------------------------------------------------------


def measure(path: Path, engine, engine_name: str, pages: tuple[int, int] | None = None) -> dict:
    source = pymupdf.open(path)
    if pages:
        selected = pymupdf.open()
        selected.insert_pdf(source, from_page=pages[0], to_page=pages[1])
        source = pymupdf.open("pdf", selected.tobytes())
    original = pymupdf.open("pdf", source.tobytes())

    result = translate_document(source, engine)
    with tempfile.TemporaryDirectory() as tmp:
        output_path = Path(tmp) / "out.pdf"
        with result.timed("save"):
            save(source, output_path)
        output_bytes = output_path.stat().st_size
        output = pymupdf.open(output_path)
        metrics = {
            **_coverage(original, result),
            **_fit(result),
            **_styles(result),
            **_fonts(result),
            **_integrity(original, output, len(original.tobytes()), output_bytes),
            **_layout(original, output),
            **_searchable(output, result),
            **_kept(output, result),
        }
    if engine_name not in ("identity",):
        metrics |= _translation(result)
    return {
        "pages": len(original),
        "paragraphs": len(result.translated),
        "metrics": {k: round(v, 3) if isinstance(v, float) else v for k, v in metrics.items()},
        "seconds": {k: round(v, 2) for k, v in result.seconds.items()},
    }


def _nonspace(text: str) -> int:
    return sum(1 for c in text if not c.isspace())


def _coverage(original: pymupdf.Document, result) -> dict:
    total = sum(_nonspace(page.get_text(flags=TEXT_FLAGS)) for page in original)
    translated = sum(_nonspace(u.text) for u in result.units)
    # Left untouched on purpose (code, markers, rotated text): not a loss.
    kept = sum(_nonspace(b.text) for page in result.pages for b in page if b.action == Action.KEEP)
    return {
        "translated_pct": 100 * translated / max(total, 1),
        "kept_pct": 100 * kept / max(total, 1),
    }


def _fit(result) -> dict:
    placements = result.placements
    count = max(len(placements), 1)
    return {
        "full_size_pct": 100 * sum(p.scale >= 0.999 for p in placements) / count,
        "scaled_below_90_pct": 100 * sum(p.scale < 0.9 for p in placements) / count,
        "below_6pt": sum(
            p.paragraph.source.lines[0].spans[0].size * p.scale < 6 for p in placements
        ),
        "not_fitted": sum(not p.fitted for p in placements),
    }


def _styles(result) -> dict:
    expected = carried = 0
    for translated, unit in zip(result.translated, result.units):
        base = base_style(paragraph_runs(translated.source))
        styled = sum(1 for run in translated.runs if run.style != base and run.text.strip())
        expected += len(unit.segments)
        carried += min(styled, len(unit.segments))
    return {"styles_carried_pct": 100 * carried / expected if expected else 100.0}


def _fonts(result) -> dict:
    total = original = 0
    for translated in result.translated:
        for run in translated.runs:
            chars = _nonspace(run.text)
            total += chars
            if result.fonts.family(run.style.font).startswith("embedded"):
                original += chars
    return {"original_font_chars_pct": 100 * original / max(total, 1)}


def _integrity(original, output, input_bytes: int, output_bytes: int) -> dict:
    def links(doc, internal: bool) -> set[tuple[int, str]]:
        # What each page links to: a link drawn in pieces (one per word, or
        # per line once the text wraps) counts once.
        return {
            (page.number, link_key(link))
            for page in doc
            for link in page.get_links()
            if (link["kind"] != pymupdf.LINK_URI) == internal and link["kind"] != pymupdf.LINK_NONE
        }

    def images(doc) -> int:
        return len({image[0] for page in doc for image in page.get_images(full=True)})

    def drawings(doc) -> int:
        return sum(len(page.get_drawings()) for page in doc)

    def rotated(doc) -> list[str]:
        return [
            text
            for page in doc
            for block in page.get_text("dict")["blocks"]
            for line in block.get("lines", [])
            if abs(line["dir"][1]) > 0.01
            and (text := "".join(s["text"] for s in line["spans"]).strip())
        ]

    return {
        "drawings_lost": max(0, drawings(original) - drawings(output)),
        "images_lost": max(0, images(original) - images(output)),
        "uri_links_lost": len(links(original, False) - links(output, False)),
        "internal_links_lost": len(links(original, True) - links(output, True)),
        # Lines not found intact: cut in pieces or rewritten horizontally.
        "rotated_lines_lost": sum(1 for line in rotated(original) if line not in rotated(output)),
        "blank_chars": sum(_blank_chars(page) for page in output),
        "overlaps_added": max(0, sum(map(_overlaps, output)) - sum(map(_overlaps, original))),
        "size_ratio": output_bytes / max(input_bytes, 1),
    }


def _blank_chars(page: pymupdf.Page, dpi: int = 150) -> int:
    """Letters and digits whose box on the rendered page holds no drawing.

    Measured on the result, not the font: it catches glyphs whose outline is
    empty whatever the font, encoding or background color.
    """
    pixmap = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width)
    zoom = dpi / 72
    blank = 0
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                for char in span["chars"]:
                    if not char["c"].isalnum():
                        continue
                    x0, y0, x1, y1 = (int(v * zoom) for v in char["bbox"])
                    region = image[max(y0, 0) : max(y1, 0), max(x0, 0) : max(x1, 0)]
                    if region.size >= 4 and region.std() < 8:
                        blank += 1
    return blank


def _overlaps(page: pymupdf.Page) -> int:
    """Pairs of text lines that collide.

    Line boxes include room for accents and descenders, so consecutive lines
    touch in normal text, more so in fonts with tall accented capitals. The
    middle half of each box is compared instead: that part is ink.
    """
    rects = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            if "".join(s["text"] for s in line["spans"]).strip():
                rect = pymupdf.Rect(line["bbox"])
                inset = rect.height * 0.25
                rects.append(rect + (0, inset, 0, -inset))
    count = 0
    for i, a in enumerate(rects):
        for b in rects[i + 1 :]:
            if not (a & b).is_empty:
                count += 1
    return count


def _layout(original, output) -> dict:
    """Correlation of the ink profile along rows and columns, per page.

    1.0 means text and graphics occupy the same bands as in the original.
    """
    rows, columns = [], []
    for a, b in zip(original, output):
        ink_a, ink_b = _ink(a), _ink(b)
        for axis, store in ((1, rows), (0, columns)):
            x, y = ink_a.sum(axis=axis), ink_b.sum(axis=axis)
            if x.std() and y.std():
                store.append(float(np.corrcoef(x, y)[0, 1]))
    return {
        "layout_row_corr_mean": statistics.mean(rows) if rows else 1.0,
        "layout_row_corr_min": min(rows) if rows else 1.0,
        "layout_col_corr_mean": statistics.mean(columns) if columns else 1.0,
    }


def _ink(page: pymupdf.Page) -> np.ndarray:
    pixmap = page.get_pixmap(dpi=36, colorspace=pymupdf.csGRAY)
    image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width)
    return 255 - image.astype(float)


def _searchable(output, result) -> dict:
    """Share of paragraphs whose translated text can be found in the output."""

    def normalize(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip().lower()

    by_paragraph = {id(t.source): t for t in result.translated}
    found = checked = 0
    for page, paragraphs in zip(output, result.pages):
        text = normalize(page.get_text(flags=TEXT_FLAGS))
        for paragraph in (p for p in paragraphs if p.action == Action.TRANSLATE):
            translated = normalize("".join(r.text for r in by_paragraph[id(paragraph)].runs))[:25]
            if len(translated) >= 8:
                checked += 1
                found += translated in text
    return {"searchable_pct": 100 * found / checked if checked else 100.0}


def _kept(output, result) -> dict:
    """Lines analysis decided to keep (code, markers, rotated text) that are
    missing from the output: drawing must never remove them."""

    def compact(text: str) -> str:
        return re.sub(r"\s+", "", text)

    lost = 0
    for page, blocks in zip(output, result.pages):
        text = compact(page.get_text(flags=TEXT_FLAGS))
        lost += sum(
            compact(line.text) not in text
            for block in blocks
            if block.action == Action.KEEP
            for line in block.lines
        )
    return {"kept_lines_lost": lost}


def _translation(result) -> dict:
    from doc_translator.translators.checks import lost_numbers

    pairs = [
        (u.text, t.text)
        for u, t in zip(result.units, result.translations)
        if sum(c.isalpha() for c in u.text) >= 4
    ]
    ratios = [len(t) / len(s) for s, t in pairs if len(s) >= 15]
    count = max(len(pairs), 1)
    return {
        "numbers_lost_pct": 100 * sum(bool(lost_numbers(s, t)) for s, t in pairs) / count,
        "unchanged_pct": 100 * sum(s == t for s, t in pairs) / count,
        "expansion_median": statistics.median(ratios) if ratios else 1.0,
    }


# -- baseline ------------------------------------------------------------------


def regressions(measured: dict, baseline: dict) -> list[str]:
    """Metrics that got worse than the baseline, beyond rounding noise."""
    problems = []
    for name, value in measured.items():
        if name not in baseline:
            continue
        reference = baseline[name]
        tolerance = 0.01 if abs(reference) <= 1.5 else 0.5
        if name in HIGHER_IS_BETTER and value < reference - tolerance:
            problems.append(f"{name}: {value} < baseline {reference}")
        if name in LOWER_IS_BETTER and value > reference + tolerance:
            problems.append(f"{name}: {value} > baseline {reference}")
    return problems


def load_baseline() -> dict:
    return json.loads(BASELINE.read_text()) if BASELINE.exists() else {}


# -- command line --------------------------------------------------------------

SUMMARY = [
    ("translated_pct", "transl%"),
    ("kept_pct", "kept%"),
    ("full_size_pct", "full%"),
    ("scaled_below_90_pct", "<90%"),
    ("styles_carried_pct", "styles%"),
    ("original_font_chars_pct", "origfont%"),
    ("blank_chars", "blank"),
    ("drawings_lost", "draw-"),
    ("internal_links_lost", "ilink-"),
    ("rotated_lines_lost", "rot-"),
    ("overlaps_added", "overlap+"),
    ("layout_row_corr_mean", "rowcorr"),
    ("size_ratio", "size×"),
    ("numbers_lost_pct", "num-%"),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--engine", nargs="+", default=list(DETERMINISTIC_ENGINES),
                        choices=["identity", "pseudo", "fast", "llm"])
    parser.add_argument("--fixtures", action="store_true", help="synthetic fixtures (default if nothing else is chosen)")
    parser.add_argument("--corpus", action="store_true", help="public corpus, downloaded on demand")
    parser.add_argument("--doc", action="append", default=[], metavar="PATH[:FIRST-LAST]",
                        help="any local PDF, optionally a 0-based page range")
    parser.add_argument("--json", type=Path, help="write all measurements to this file")
    parser.add_argument("--update-baseline", action="store_true",
                        help="store the measurements of deterministic engines as the new baseline")
    args = parser.parse_args()

    documents: list[tuple[str, Path, tuple[int, int] | None]] = []
    if args.fixtures or not (args.corpus or args.doc):
        with tempfile.TemporaryDirectory(delete=False) as tmp:
            for name, path in fixtures.build(Path(tmp)).items():
                documents.append((name, path, None))
    if args.corpus:
        for document in corpus.CORPUS:
            documents.append((document.name, corpus.fetch(document), document.pages))
    for spec in args.doc:
        path, _, span = spec.partition(":")
        pages = tuple(int(n) for n in span.split("-")) if span else None
        documents.append((Path(path).stem, Path(path), pages))

    baseline = load_baseline()
    measurements: dict[str, dict] = {}
    header = f"{'document':16} {'engine':8} " + " ".join(f"{label:>9}" for _, label in SUMMARY)
    print(header)
    for engine_name in args.engine:
        engine = make_engine(engine_name)
        for name, path, pages in documents:
            key = f"{name}/{engine_name}"
            measured = measure(path, engine, engine_name, pages)
            measurements[key] = measured
            values = measured["metrics"]
            print(f"{name:16} {engine_name:8} " + " ".join(
                f"{_fmt(values.get(metric)):>9}" for metric, _ in SUMMARY))
            for problem in regressions(values, baseline.get(key, {})):
                print(f"    REGRESSION {problem}")

    if args.json:
        args.json.write_text(json.dumps(measurements, indent=1, ensure_ascii=False))
    if args.update_baseline:
        updated = load_baseline()
        for key, measured in measurements.items():
            if key.split("/")[1] in DETERMINISTIC_ENGINES:
                updated[key] = measured["metrics"]
        BASELINE.write_text(json.dumps(updated, indent=1, sort_keys=True) + "\n")
        print(f"baseline written to {BASELINE}")


def _fmt(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.2f}" if abs(value) < 10 else f"{value:.1f}"
    return str(value)


if __name__ == "__main__":
    sys.exit(main())
