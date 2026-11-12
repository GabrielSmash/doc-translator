"""What a run changed, and what went wrong, per page.

Built from the pipeline's `Result` at the end of a run: the CLI prints its
summary and can write it as JSON (`--report`).
"""

import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .analysis import Action
from .fonts import SUBSET_PREFIX

FULL_SIZE = 0.999  # scales above this count as the original size
MAX_LISTED = 8  # pages or fonts named in the summary before "..."
KEPT_NAMES = {
    "marker": "list markers",
    "symbols": "lines of symbols",
    "code": "code blocks",
    "rotated": "rotated lines",
}


@dataclass
class PageReport:
    page: int  # 1-based, as people count pages
    paragraphs: int  # blocks translated
    kept: dict[str, int]  # blocks left as they are, by kind (code, marker...)
    reduced: int  # paragraphs drawn smaller than the original font size
    not_fitted: int  # paragraphs that overflowed their box even reduced
    error: str | None = None  # the page was left untranslated


@dataclass
class Report:
    source: str
    output: str
    pages: list[PageReport]
    chars: int  # letters and digits in the text sent to translation
    chars_translated: int  # of those, in paragraphs the engine changed
    fonts_reused: list[str]
    fonts_replaced: list[str]  # drawn with a similar fallback font instead
    warnings: list[str] = field(default_factory=list)
    seconds: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_result(cls, result, source: Path, output: Path) -> "Report":
        by_page: dict[int, Counter] = {}
        page_of = {id(p): index for index, page in enumerate(result.pages) for p in page}
        for placement in result.placements:
            counts = by_page.setdefault(page_of[id(placement.paragraph.source)], Counter())
            counts["reduced"] += placement.scale < FULL_SIZE
            counts["not_fitted"] += not placement.fitted
        pages = [
            PageReport(
                page=index + 1,
                paragraphs=sum(b.action == Action.TRANSLATE for b in blocks),
                kept=dict(Counter(str(b.kind) for b in blocks if b.action != Action.TRANSLATE)),
                reduced=by_page.get(index, Counter())["reduced"],
                not_fitted=by_page.get(index, Counter())["not_fitted"],
                error=result.failures.get(index),
            )
            for index, blocks in enumerate(result.pages)
        ]

        chars = translated = 0
        for unit, translation in zip(result.units, result.translations):
            count = sum(c.isalnum() for c in unit.text)
            chars += count
            if translation.text.strip() != unit.text.strip():
                translated += count

        used = list(dict.fromkeys(run.style.font for t in result.translated for run in t.runs))
        reused = set(result.fonts.reused) if result.fonts else set()
        return cls(
            source=str(source),
            output=str(output),
            pages=pages,
            chars=chars,
            chars_translated=translated,
            fonts_reused=_font_names(f for f in used if f in reused),
            fonts_replaced=_font_names(f for f in used if f not in reused),
            warnings=list(result.warnings),
            seconds={k: round(v, 2) for k, v in result.seconds.items()},
        )

    @property
    def failed_pages(self) -> list[PageReport]:
        return [p for p in self.pages if p.error]

    def summary(self) -> str:
        paragraphs = sum(p.paragraphs for p in self.pages)
        share = 100 * self.chars_translated / self.chars if self.chars else 0
        seconds = sum(self.seconds.values())
        lines = [
            f"{len(self.pages)} pages, {paragraphs} paragraphs, "
            f"{share:.0f}% of the text translated, in {seconds:.0f} s"
        ]
        kept: Counter[str] = Counter()
        for page in self.pages:
            kept.update(page.kept)
        if kept:
            kinds = ", ".join(f"{count} {KEPT_NAMES.get(kind, kind)}" for kind, count in kept.most_common())
            lines.append(f"  Left as they are: {kinds}")
        reduced = [p for p in self.pages if p.reduced]
        if reduced:
            count = sum(p.reduced for p in reduced)
            lines.append(
                f"  Smaller font: {count} paragraphs, on pages {_pages(reduced)}"
                " (the translation is longer than the room it had)"
            )
        not_fitted = [p for p in self.pages if p.not_fitted]
        if not_fitted:
            count = sum(p.not_fitted for p in not_fitted)
            lines.append(f"  Did not fit even reduced: {count} paragraphs, on pages {_pages(not_fitted)}")
        if self.fonts_replaced:
            lines.append(
                f"  Fonts replaced by a similar one: {_listed(self.fonts_replaced)}"
                f" ({len(self.fonts_reused)} kept)"
            )
        if self.warnings:
            lines.append(f"  Warnings: {len(self.warnings)} (listed with --report FILE)")
        for page in self.failed_pages:
            lines.append(f"  Page {page.page} left untranslated: {page.error}")
        return "\n".join(lines)

    def write(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2) + "\n")


def _font_names(fonts) -> list[str]:
    return sorted({SUBSET_PREFIX.sub("", f) for f in fonts})


def _pages(pages: list[PageReport]) -> str:
    return _listed([str(p.page) for p in pages])


def _listed(items: list[str]) -> str:
    shown = ", ".join(items[:MAX_LISTED])
    return shown + (f" and {len(items) - MAX_LISTED} more" if len(items) > MAX_LISTED else "")
