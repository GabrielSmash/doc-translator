"""Find defects by class: rules every translated page must keep.

Each check looks for one kind of defect anywhere in the output, so a fix is
judged by how many cases of its class remain on the whole corpus, not by
the example that was noticed. measure.py stores the counts in the baseline.

Checks, and the stage they watch:

- split_lines (analysis): one line of text read as pieces of several
  paragraphs, each translated on its own.
- overlapping_text (layout): written lines that run into other text.
- crossed_rules (layout): written lines that cross a table or frame rule
  the original line did not cross (text out of its cell).
- misplaced_leaders (drawing): table-of-contents page numbers not on their
  own entry's row.
- lost_alignment (analysis): a column of centered or right-aligned cells
  whose translations are no longer aligned.
- grown_cells (translation): a one-line cell whose translation needs more
  lines or a smaller font.
- headings_in_cells (analysis): table cells taken for section headings.

Usage:
    uv run python -m tools.defects --doc samples/7700.pdf --engine fast
"""

import argparse
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from doc_translator.analysis import Action, Kind
from doc_translator.analysis.graphics import separating_rules
from doc_translator.pdf.read import read_page

CHECKS = (
    "split_lines",
    "overlapping_text",
    "crossed_rules",
    "misplaced_leaders",
    "lost_alignment",
    "grown_cells",
    "headings_in_cells",
)
# Gaps between words of one line reach this many font sizes in justified text.
WORD_GAP = 1.5
# Positions within this distance (points) are the same.
ALIGNED = 1.5


@dataclass
class Finding:
    check: str
    page: int  # 1-based
    text: str
    detail: str = ""

    def __str__(self) -> str:
        detail = f" ({self.detail})" if self.detail else ""
        return f"p{self.page} {self.check}: {self.text[:70]!r}{detail}"


def find(original: pymupdf.Document, output: pymupdf.Document, result) -> list[Finding]:
    """Defects of a pipeline result: `original` is the document before
    translation, `output` the translated one."""
    by_source = {id(p.paragraph.source): p for p in result.placements}
    findings = []
    for index, blocks in enumerate(result.pages):
        page = original[index]
        rules = _rules(page)
        placed = [by_source[id(b)] for b in blocks if id(b) in by_source]
        findings += [
            *_split_lines(index, blocks, rules),
            *_overlapping_text(index, placed, blocks),
            *_crossed_rules(index, placed, rules, page.rect),
            *_lost_alignment(index, placed, rules),
            *_grown_cells(index, placed, rules),
            *_headings_in_cells(index, blocks, rules),
        ]
    findings += _misplaced_leaders(output, result)
    return findings


def counts(findings: list[Finding]) -> dict[str, int]:
    return {check: sum(f.check == check for f in findings) for check in CHECKS}


# -- geometry ------------------------------------------------------------------


def _rules(page: pymupdf.Page) -> list[pymupdf.Rect]:
    """Table and frame rules of the original page, underlines left out."""
    content = read_page(page)
    lines = [line for block in content.blocks for line in block.lines]
    return separating_rules(lines, content.drawings)


def _ink(rect: pymupdf.Rect) -> pymupdf.Rect:
    """The middle of a line box: line boxes hold room for accents and touch
    their neighbors in normal text; their middle is ink."""
    inset = rect.height * 0.25
    return pymupdf.Rect(rect.x0 + 0.5, rect.y0 + inset, rect.x1 - 0.5, rect.y1 - inset)


def _crosses(ink: pymupdf.Rect, rule: pymupdf.Rect) -> bool:
    if rule.width < rule.height:  # vertical
        x = (rule.x0 + rule.x1) / 2
        return ink.x0 < x < ink.x1 and rule.y0 < ink.y1 and rule.y1 > ink.y0
    y = (rule.y0 + rule.y1) / 2
    return ink.y0 < y < ink.y1 and rule.x0 < ink.x1 and rule.x1 > ink.x0


def _cell(rect: pymupdf.Rect, rules: list[pymupdf.Rect]) -> pymupdf.Rect | None:
    """The box of rules closely around a line: a table cell, or None."""
    middle = (rect.y0 + rect.y1) / 2
    reach = rect.height * 1.5
    vertical = [r for r in rules if r.width < r.height and r.y0 <= middle <= r.y1]
    horizontal = [r for r in rules if r.width >= r.height and r.x0 <= rect.x0 + 1 and r.x1 >= rect.x1 - 1]
    left = [r.x1 for r in vertical if r.x1 <= rect.x0 + 1]
    right = [r.x0 for r in vertical if r.x0 >= rect.x1 - 1]
    above = [r.y1 for r in horizontal if rect.y0 - reach <= r.y1 <= middle]
    below = [r.y0 for r in horizontal if middle <= r.y0 <= rect.y1 + reach]
    if not (left and right and above and below):
        return None
    return pymupdf.Rect(max(left), max(above), min(right), min(below))


# -- checks ----------------------------------------------------------------------


def _split_lines(index: int, blocks, rules) -> list[Finding]:
    lines = [(b, line) for b in blocks if b.action == Action.TRANSLATE for line in b.lines]
    found = []
    reported = set()
    for i, (block_a, a) in enumerate(lines):
        for block_b, b in lines[i + 1 :]:
            if block_a is block_b or (id(block_a), id(block_b)) in reported:
                continue
            if abs(a.baseline - b.baseline) > 1 or not a.horizontal or not b.horizontal:
                continue
            size = max(s.size for s in a.spans)
            left, right = (a, b) if a.bbox.x0 < b.bbox.x0 else (b, a)
            gap = right.bbox.x0 - left.bbox.x1
            if not -1 <= gap <= WORD_GAP * size:
                continue
            between = pymupdf.Rect(left.bbox.x1 - 0.5, left.bbox.y0, right.bbox.x0 + 0.5, left.bbox.y1)
            if any(_crosses(_ink(between), r) for r in rules if r.width < r.height):
                continue
            # The gap is between words of running text when a line just above
            # or below runs across it; labels side by side have none.
            reach = 1.5 * size
            if not any(
                other.bbox.x0 < between.x0 and other.bbox.x1 > between.x1
                and 0 < abs(other.baseline - a.baseline) <= reach
                for _, other in lines
            ):
                continue
            reported.add((id(block_a), id(block_b)))
            found.append(Finding("split_lines", index + 1, f"{left.text} | {right.text}"))
    return found


def _overlapping_text(index: int, placed, blocks) -> list[Finding]:
    # Kept text (code, markers) is written where it was: its source lines.
    written = [(p.paragraph.source, [line.rect for line in p.lines]) for p in placed]
    written += [(b, [line.bbox for line in b.lines]) for b in blocks if b.action != Action.TRANSLATE]
    found = []
    for i, (source_a, rects_a) in enumerate(written):
        for source_b, rects_b in written[i + 1 :]:
            if not any(_ink(a).intersects(_ink(b)) for a in rects_a for b in rects_b):
                continue
            # Text that already overlapped in the original is not a new defect.
            if any(_ink(a.bbox).intersects(_ink(b.bbox)) for a in source_a.lines for b in source_b.lines):
                continue
            found.append(Finding("overlapping_text", index + 1, f"{source_a.text[:35]} | {source_b.text[:35]}"))
    return found


def _crossed_rules(index: int, placed, rules, page_rect) -> list[Finding]:
    found = []
    for placement in placed:
        source = placement.paragraph.source
        crossed = {
            i
            for line in placement.lines
            for i, rule in enumerate(rules)
            if _crosses(_ink(line.rect), rule)
        }
        crossed -= {i for line in source.lines for i, rule in enumerate(rules) if _crosses(_ink(line.bbox), rule)}
        outside = any(not page_rect.contains(_ink(line.rect)) for line in placement.lines)
        # Below the last rule of a table nothing is crossed: the text has
        # still left its cell.
        cell = _cell(source.lines[0].bbox, rules) if len(source.lines) == 1 else None
        escaped = cell is not None and any(
            not (cell + (-1, -1, 1, 1)).contains(_ink(line.rect)) for line in placement.lines
        )
        if crossed or outside or escaped:
            detail = "off the page" if outside else f"{len(crossed)} rules" if crossed else "out of its cell"
            found.append(Finding("crossed_rules", index + 1, source.text, detail))
    return found


def _lost_alignment(index: int, placed, rules) -> list[Finding]:
    """Columns of one-line table cells aligned on their centers (or right
    edges) and not on their left edges, whose translations lost that
    alignment."""
    single = [
        p
        for p in placed
        if len(p.paragraph.source.lines) == 1 and p.lines and _cell(p.paragraph.source.bbox, rules)
    ]
    found = []
    for name, edge in (("center", lambda r: (r.x0 + r.x1) / 2), ("right", lambda r: r.x1)):
        groups: list[list] = []
        for placement in sorted(single, key=lambda p: edge(p.paragraph.source.bbox)):
            value = edge(placement.paragraph.source.bbox)
            if groups and abs(edge(groups[-1][0].paragraph.source.bbox) - value) <= ALIGNED:
                groups[-1].append(placement)
            else:
                groups.append([placement])
        for group in groups:
            lefts = [p.paragraph.source.bbox.x0 for p in group]
            if len(group) < 3 or max(lefts) - min(lefts) <= 2 * ALIGNED:
                continue  # left-aligned too, or too few to call a column
            for placement in group:
                moved = edge(placement.lines[0].rect) - edge(placement.paragraph.source.bbox)
                if abs(moved) > 2 * ALIGNED:
                    found.append(Finding("lost_alignment", index + 1, placement.paragraph.source.text, f"{name}, {moved:+.1f} pt"))
    return found


def _grown_cells(index: int, placed, rules) -> list[Finding]:
    found = []
    for placement in placed:
        source = placement.paragraph.source
        if len(source.lines) != 1 or _cell(source.bbox, rules) is None:
            continue
        if len(placement.lines) > 1 or placement.scale < 0.85:
            detail = f"{len(placement.lines)} lines, {placement.scale:.0%}"
            found.append(Finding("grown_cells", index + 1, source.text, detail))
    return found


def _headings_in_cells(index: int, blocks, rules) -> list[Finding]:
    """Headings in a table: a cell with a neighbor cell holding text. A
    heading framed on its own (a title in a box) is not one."""
    cells = [(b, _cell(line.bbox, rules)) for b in blocks for line in b.lines]
    cells = [(b, c) for b, c in cells if c is not None]

    def adjacent(a: pymupdf.Rect, b: pymupdf.Rect) -> bool:
        side = (abs(a.x1 - b.x0) < 2 or abs(b.x1 - a.x0) < 2) and a.y0 < b.y1 and b.y0 < a.y1
        stacked = (abs(a.y1 - b.y0) < 2 or abs(b.y1 - a.y0) < 2) and a.x0 < b.x1 and b.x0 < a.x1
        return side or stacked

    return [
        Finding("headings_in_cells", index + 1, block.text)
        for block, cell in cells
        if block.kind == Kind.HEADING
        and len(block.lines) == 1
        and any(other is not block and adjacent(cell, c) for other, c in cells)
    ]


def _misplaced_leaders(output, result) -> list[Finding]:
    """Page numbers must end each entry: on the row of its last line, as
    written (a long translated title may take one more line)."""
    by_source = {id(p.paragraph.source): p for p in result.placements}
    found = []
    for index, (page, blocks) in enumerate(zip(output, result.pages)):
        words = page.get_text("words")
        for block in blocks:
            leader = block.leader
            if leader is None or not leader.number:
                continue
            placement = by_source.get(id(block))
            row = placement.lines[-1].rect if placement and placement.lines else block.lines[-1].bbox
            # Words ending where page numbers end, on this entry's row.
            on_row = [
                w[4]
                for w in words
                if abs(w[2] - leader.right) <= 2 and row.y0 < (w[1] + w[3]) / 2 < row.y1
            ]
            numbers = [w.lstrip(".") for w in on_row]
            if numbers != [leader.number]:
                found.append(Finding("misplaced_leaders", index + 1, block.text, f"found {numbers}"))
    return found


# -- command line ----------------------------------------------------------------


def main() -> None:
    from doc_translator.pipeline import translate_document
    from tools.measure import make_engine

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--doc", required=True, metavar="PATH[:FIRST-LAST]")
    parser.add_argument("--engine", default="pseudo", choices=["identity", "pseudo", "fast", "llm"])
    parser.add_argument("--check", choices=CHECKS, help="list only this check's findings")
    parser.add_argument("--save", metavar="PDF", help="also save the translated document")
    args = parser.parse_args()

    path, _, span = args.doc.partition(":")
    source = pymupdf.open(path)
    if span:
        first, last = (int(n) for n in span.split("-"))
        selected = pymupdf.open()
        selected.insert_pdf(source, from_page=first, to_page=last)
        source = pymupdf.open("pdf", selected.tobytes())
    original = pymupdf.open("pdf", source.tobytes())
    result = translate_document(source, make_engine(args.engine))
    findings = find(original, source, result)
    if args.save:
        from doc_translator.pipeline import save

        save(source, Path(args.save))
    for finding in findings:
        if args.check in (None, finding.check):
            print(finding)
    print(" ".join(f"{check}={count}" for check, count in counts(findings).items()))


if __name__ == "__main__":
    main()
