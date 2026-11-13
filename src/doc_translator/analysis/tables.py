"""The tables of a page, as text: context for translating their cells.

A cell alone ("Pin #", "White/Orange", "no") says too little to translate
it well or the same way as its neighbors: "Pin" became "Pinho" (pine), and
"White" was kept in English in one cell and translated in the next. With
the whole table, the engine sees the header and every row.
"""

from .model import Block, Kind

# Rows further apart than this many line heights start another table.
TABLE_GAP = 2.0
# Rows on one baseline, within this distance (points).
ROW_TOLERANCE = 2.0
# Longer tables are cut: the prompt must leave room for the cells to translate.
MAX_CHARS = 2000


def tables(blocks: list[Block]) -> list[list[Block]]:
    """The table cells of one page, grouped into tables (top to bottom)."""
    cells = sorted((b for b in blocks if b.kind == Kind.TABLE_CELL), key=lambda b: b.bbox.y0)
    groups: list[list[Block]] = []
    bottom = 0.0
    for cell in cells:
        rect = cell.bbox
        if groups and rect.y0 <= bottom + TABLE_GAP * cell.lines[0].bbox.height:
            groups[-1].append(cell)
            bottom = max(bottom, rect.y1)
        else:
            groups.append([cell])
            bottom = rect.y1
    return groups


def table_text(cells: list[Block]) -> str:
    """One line per row, cells separated by " | ", left to right."""
    rows: list[list[Block]] = []
    for cell in sorted(cells, key=lambda b: b.lines[0].baseline):
        if rows and abs(cell.lines[0].baseline - rows[-1][0].lines[0].baseline) <= ROW_TOLERANCE:
            rows[-1].append(cell)
        else:
            rows.append([cell])
    lines = [" | ".join(c.text for c in sorted(row, key=lambda b: b.bbox.x0)) for row in rows]
    text = ""
    for line in lines:
        if len(text) + len(line) > MAX_CHARS:
            break
        text += line + "\n"
    return text.strip()
