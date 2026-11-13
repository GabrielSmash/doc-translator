"""The kind of each block, and the lines that are kept as they are.

Kinds of translated blocks are descriptive (the report counts them; later
stages may treat headings or table cells differently). Kinds of kept lines
decide the action: that text is never removed nor redrawn.
"""

import re
from collections import Counter

from ..fonts import MONO_NAMES
from .cells import in_table
from .model import FLAG_MONOSPACED, Action, Block, Kind, Line

CAPTION = re.compile(r"^(?:Figure|Fig\.|Table)\s+[\dA-Z]+(?:[.\-]\d+)*\s*[.:]", re.IGNORECASE)
# A heading is set this much larger than the body text of its page, or in bold.
HEADING_SIZE_RATIO = 1.15
HEADING_MAX_LINES = 3
# Blocks whose first lines share a baseline (in points) are side by side.
BASELINE_TOLERANCE = 1.0
# This many blocks side by side form a table row. Two are not enough: a
# section number and its title, a page header split left and right, the
# columns of a two-column page all stand in pairs.
TABLE_MIN_COLUMNS = 3


def kept_kind(line: Line) -> Kind | None:
    """CODE or ROTATED for lines that are not translated; None otherwise."""
    if not line.horizontal:
        # Labels along an axis, sideways table headers: rewriting them
        # horizontally cut them and spilled them over their neighbors.
        return Kind.ROTATED
    spans = [s for s in line.spans if s.text.strip()]
    if all(s.flags & FLAG_MONOSPACED or any(h in s.font.lower() for h in MONO_NAMES) for s in spans):
        return Kind.CODE  # commands and terminal output must stay as typed
    return None


def kept_block(lines: list[Line], kind: Kind) -> Block:
    return Block(lines=lines, kind=kind, action=Action.KEEP)


def classify(blocks: list[Block]) -> None:
    """Set the kind of the translated blocks of one page."""
    translated = [b for b in blocks if b.action == Action.TRANSLATE]
    body = _body_size(translated)
    # Side by side on a baseline, or boxed in by rules next to other boxes.
    cells = _table_cells(translated) | in_table({id(b): b.cell for b in translated if b.cell is not None})
    for block in translated:
        if block.leader:
            block.kind = Kind.TOC_ENTRY
        elif block.prefix:
            block.kind = Kind.LIST_ITEM
        elif id(block) in cells:
            block.kind = Kind.TABLE_CELL
        elif CAPTION.match(block.text):
            block.kind = Kind.CAPTION
        elif _is_heading(block, body):
            block.kind = Kind.HEADING


def _body_size(blocks: list[Block]) -> float:
    """The font size most of the page's text is set in."""
    sizes: Counter[float] = Counter()
    for block in blocks:
        for span in block.spans:
            sizes[round(span.size, 1)] += len(span.text.strip())
    return sizes.most_common(1)[0][0] if sizes else 0.0


def _is_heading(block: Block, body: float) -> bool:
    text = block.text.strip()
    if len(block.lines) > HEADING_MAX_LINES or text.endswith(".") or sum(c.isalpha() for c in text) < 2:
        return False
    spans = [s for s in block.spans if s.text.strip()]
    larger = max(s.size for s in spans) >= HEADING_SIZE_RATIO * body
    return larger or all(s.bold for s in spans)


def _table_cells(blocks: list[Block]) -> set[int]:
    """Blocks standing side by side on a common baseline: cells of a row.

    A heuristic: two-column tables are not recognized (see TABLE_MIN_COLUMNS).
    """
    rows: list[list[Block]] = []
    for block in sorted(blocks, key=lambda b: b.lines[0].baseline):
        if rows and block.lines[0].baseline - rows[-1][0].lines[0].baseline <= BASELINE_TOLERANCE:
            rows[-1].append(block)
        else:
            rows.append([block])
    cells: set[int] = set()
    for row in rows:
        row = _apart(row)
        if len(row) >= TABLE_MIN_COLUMNS:
            cells.update(id(b) for b in row)
    return cells


def _apart(row: list[Block]) -> list[Block]:
    """The blocks of `row` that don't overlap another one horizontally."""
    return [
        b
        for b in row
        if any(o is not b for o in row)
        and all(o is b or b.bbox.x1 <= o.bbox.x0 or o.bbox.x1 <= b.bbox.x0 for o in row)
    ]
