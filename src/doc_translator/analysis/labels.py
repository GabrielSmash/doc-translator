"""Labels a PDF stores as separate blocks, one per line, joined back.

A diagram's label of two lines ("Diagnositic" over "LEDs", 7700 manual,
page 5) can come as two PyMuPDF blocks. Translated apart, the words keep
the source's order: "Diagnóstico LEDs" for "LEDs de diagnóstico".
"""

import pymupdf

from ..pdf.read import row_rules
from .model import TextBlock

# A label has at most this many words (layout.LABEL_WORDS).
LABEL_WORDS = 4
# Lines of one label: the next starts within this many font sizes of the
# previous one's bottom, and their centers are this close (points).
LINE_GAP = 0.5
CENTER_TOLERANCE = 2.0


def join_stacked_labels(blocks: list[TextBlock], drawings: list[dict]) -> list[TextBlock]:
    """`blocks` with each label split into blocks joined into one.

    Only lines centered on each other and of different widths: lines of a
    list or a table column share their left (or right) edge instead. Lines
    with a rule between them are cells of a table.
    """
    rules = [rule for drawing in drawings for rule in row_rules(drawing)]
    joined: list[TextBlock] = []
    for block in blocks:
        upper = next((b for b in reversed(joined) if _continues(b, block, rules)), None)
        if upper is None:
            joined.append(block)
            continue
        joined[joined.index(upper)] = TextBlock(upper.bbox | block.bbox, [*upper.lines, *block.lines])
    return joined


def _continues(upper: TextBlock, lower: TextBlock, rules: list[pymupdf.Rect]) -> bool:
    if not upper.lines or not lower.lines:
        return False
    above, below = upper.lines[-1], lower.lines[0]
    if not (above.horizontal and below.horizontal and len(lower.lines) == 1):
        return False
    words = " ".join(line.text for line in [*upper.lines, *lower.lines]).split()
    if len(words) > LABEL_WORDS or not all(any(c.isalpha() for c in line.text) for line in (above, below)):
        return False
    size = above.size
    if abs(below.size - size) > 0.5 or _bold(above) != _bold(below) or above.text.rstrip().endswith(":"):
        return False
    gap = below.bbox.y0 - above.bbox.y1
    if not -size * 0.3 <= gap <= LINE_GAP * size:
        return False
    a, b = above.bbox, below.bbox
    centered = abs((a.x0 + a.x1) / 2 - (b.x0 + b.x1) / 2) <= CENTER_TOLERANCE
    same_edge = abs(a.x0 - b.x0) <= CENTER_TOLERANCE or abs(a.x1 - b.x1) <= CENTER_TOLERANCE
    if not centered or same_edge:
        return False
    return not any(rule.y0 >= a.y1 - 1 and rule.y1 <= b.y0 + 1 and rule.x0 < a.x1 and rule.x1 > a.x0 for rule in rules)


def _bold(line) -> bool:
    return any(span.bold for span in line.visible_spans)
