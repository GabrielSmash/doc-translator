"""Cells: the box of rules around a block of text.

A table cell, a framed note, a box of a diagram. The cell is where the
block's text belongs: layout keeps the translation inside it, a one-line
block's alignment is read from its place in it, and a block in a cell with
neighbor cells is a table cell, whatever its font.
"""

import pymupdf

from .model import Block

# Positions within this distance (points) are the same.
TOLERANCE = 2.0
# Rules above and below may be this many line heights away from the text
# (cells taller than their text: a row of several lines next to it).
REACH = 2.5


def cell_around(block: Block, rules: list[pymupdf.Rect]) -> pymupdf.Rect | None:
    """The nearest rules on all four sides of the block, or None."""
    rect = block.bbox
    middle = (rect.y0 + rect.y1) / 2
    reach = REACH * block.lines[0].bbox.height
    vertical = [r for r in rules if r.height > r.width and r.y0 <= middle <= r.y1]
    left = [r.x1 for r in vertical if r.x1 <= rect.x0 + 1]
    right = [r.x0 for r in vertical if r.x0 >= rect.x1 - 1]
    if not (left and right):
        return None
    x0, x1 = max(left), min(right)
    # Rules above and below must run across the cell, not a short stroke.
    horizontal = [r for r in rules if r.width >= r.height and r.x0 <= x0 + TOLERANCE and r.x1 >= x1 - TOLERANCE]
    above = [r.y1 for r in horizontal if rect.y0 - reach <= r.y1 <= rect.y0 + 1]
    below = [r.y0 for r in horizontal if rect.y1 - 1 <= r.y0 <= rect.y1 + reach]
    if not (above and below):
        return None
    return pymupdf.Rect(x0, max(above), x1, min(below))


def in_table(cells: dict[int, pymupdf.Rect]) -> set[int]:
    """Blocks (by id) whose cell touches another cell holding text: a table.
    A box on its own is a frame (a note, a title in a box)."""

    def adjacent(a: pymupdf.Rect, b: pymupdf.Rect) -> bool:
        side = (abs(a.x1 - b.x0) <= TOLERANCE or abs(b.x1 - a.x0) <= TOLERANCE) and a.y0 < b.y1 and b.y0 < a.y1
        stacked = (abs(a.y1 - b.y0) <= TOLERANCE or abs(b.y1 - a.y0) <= TOLERANCE) and a.x0 < b.x1 and b.x0 < a.x1
        return side or stacked

    return {
        key
        for key, cell in cells.items()
        if any(other != key and cell != c and adjacent(cell, c) for other, c in cells.items())
    }


def align_in_cells(blocks: list[Block]) -> None:
    """Set the alignment of one-line blocks in cells: the edge they share
    with the other cells of their column, or else their place in the cell.

    One line says nothing about its alignment; a column of them does: the
    labels of a column start at one distance from its left rule, centered
    values have one middle. A label that nearly fills its cell looks
    centered on its own, and follows its column.
    """
    single = [b for b in blocks if b.cell is not None and len(b.lines) == 1 and b.leader is None]
    columns: list[list[Block]] = []
    for block in single:
        column = next(
            (c for c in columns if abs(c[0].cell.x0 - block.cell.x0) <= TOLERANCE and abs(c[0].cell.x1 - block.cell.x1) <= TOLERANCE),
            None,
        )
        if column is None:
            columns.append([block])
        else:
            column.append(block)
    for column in columns:
        shared = _shared_alignment(column) if len(column) >= 2 else None
        for block in column:
            block.align = shared or _alignment_in(block.cell, block.bbox)


def _shared_alignment(column: list[Block]) -> str | None:
    def same(values: list[float]) -> bool:
        return max(values) - min(values) <= TOLERANCE

    widths = [b.bbox.width for b in column]
    if same(widths):
        return None  # texts of one width line up whatever the alignment
    if same([b.bbox.x0 - b.cell.x0 for b in column]):
        return "left"
    if same([b.cell.x1 - b.bbox.x1 for b in column]):
        return "right"
    if same([(b.bbox.x0 + b.bbox.x1) / 2 for b in column]):
        return "center"
    return None


def _alignment_in(cell: pymupdf.Rect, rect: pymupdf.Rect) -> str:
    """text-align of a one-line block, from the room it leaves in its cell
    (glyph boxes and trailing spaces make the two sides differ a little)."""
    left, right = rect.x0 - cell.x0, cell.x1 - rect.x1
    if left > TOLERANCE and abs(left - right) <= max(TOLERANCE, 0.15 * (left + right)):
        return "center"
    if right <= 2 * TOLERANCE < left - TOLERANCE:
        return "right"
    return "left"
