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


# Lines of a terminal or menu screen copied into the text. Manuals set them
# in a monospaced font, but not always (the 7700FC manual has screens in
# Times Bold): their content tells them apart from prose.
SCREEN_LINE = [
    re.compile(r"^<\w{1,3}>\s+\S"),  # menu item: "<1> Network Setup"
    re.compile(r"^\(\d{1,2}\)\s.*\[.*\]$"),  # item with its value: "(1) Use DHCP [N]"
    re.compile(r"^([*=#~-])\1{2,}.*([*=#~-])\2{2,}$"),  # banner: "**** Main Menu ****"
    re.compile(r"^(?:[a-z][\w/-]*\s*[:>#$]|[>#$])$"),  # prompt: "login:", ">"
    re.compile(r"\S {3,}\S"),  # columns aligned with spaces
    re.compile(r"\b\d{1,2}:\d{2}:\d{2}\b"),  # time stamp
    re.compile(r":\s*\d{1,3}(?:\.\d{1,3}){3}$"),  # address listing: "Trap #1: 192.168.1.76"
    re.compile(r"\s>\s*\S+$"),  # prompt and the answer typed: "Remove trap # > 2"
]
# A screen is at least this many lines, most of them screen-like.
SCREEN_MIN_LINES = 2
SCREEN_SHARE = 0.5


def is_screen(lines: list[Line]) -> bool:
    """Lines of one text block that reproduce a terminal or menu screen."""
    texts = [line.text.strip() for line in lines if line.text.strip()]
    if len(texts) < SCREEN_MIN_LINES:
        return False
    like = sum(any(p.search(text) for p in SCREEN_LINE) for text in texts)
    return like >= SCREEN_SHARE * len(texts) and like >= SCREEN_MIN_LINES


def screen_part(lines: list[Line]) -> list[Line]:
    """The lines of a screen block that are the screen: those set in the
    style of its screen-like lines. A PDF block can go on with the text
    about the screen, in the body font ("To choose one of the menu
    choices, press..."): that text is translated."""
    like = [line for line in lines if any(p.search(line.text.strip()) for p in SCREEN_LINE)]
    style = {_style(s) for line in like for s in line.visible_spans}
    return [line for line in lines if all(_style(s) in style for s in line.visible_spans)]


def _style(span) -> tuple[str, float]:
    return (span.font, round(span.size, 1))


# A block this many line heights below a screen, in its style and margin,
# is the rest of the screen.
SCREEN_GAP = 3.0


def continues_screen(lines: list[Line], screen: list[Line]) -> bool:
    if not lines or not screen:
        return False
    last = screen[-1]
    style = {_style(s) for line in screen for s in line.visible_spans}
    same_style = all(_style(s) in style for line in lines for s in line.visible_spans)
    same_margin = all(abs(line.bbox.x0 - last.bbox.x0) <= 2 for line in lines)
    close = 0 <= lines[0].bbox.y0 - last.bbox.y1 <= SCREEN_GAP * last.bbox.height
    return same_style and same_margin and close


def kept_block(lines: list[Line], kind: Kind) -> Block:
    return Block(lines=lines, kind=kind, action=Action.KEEP)


def classify(blocks: list[Block]) -> None:
    """Set the kind of the translated blocks of one page."""
    translated = [b for b in blocks if b.action == Action.TRANSLATE]
    body = _body_size(translated)
    # Side by side on a baseline, or boxed in by rules next to other boxes.
    cells = _table_cells(translated) | in_table({id(b): b.cell for b in translated if b.cell is not None})
    for block in translated:
        if block.leader and block.leader.number:
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
