"""Stage 2: the page as read -> blocks, each with a kind and an action.

Every line of text of the page ends up in exactly one block.
"""

import pymupdf

from ..pdf.read import column_rules
from .blocks import split_block
from .cells import align_in_cells, cell_around
from .classify import classify, continues_screen, is_screen, kept_block, kept_kind
from .graphics import separating_rules
from .lines import join_pieces, visual_lines
from .model import Action, Block, Kind, PageContent

__all__ = ["Action", "Block", "Kind", "analyze", "analyze_page"]


def analyze_page(page: pymupdf.Page) -> list[Block]:
    from ..pdf.read import read_page

    return analyze(read_page(page))


def analyze(content: PageContent) -> list[Block]:
    kept: list[Block] = []
    groups = []  # (PyMuPDF block, its lines to translate)
    verticals = [rule for drawing in content.drawings for rule in column_rules(drawing)]
    joined = []
    for text_block in content.blocks:
        pieces = [line for raw in text_block.lines for line in visual_lines(raw, content.links)]
        pieces = [line for line in pieces if line.text.strip()]
        joined.append(join_pieces(pieces, verticals))
    screens = _screens(joined)
    for text_block, block_lines, screen in zip(content.blocks, joined, screens):
        lines: list = []
        code: list = []  # consecutive code lines form one block
        if screen:
            kept.append(kept_block(block_lines, Kind.CODE))
            continue
        for line in block_lines:
            kind = kept_kind(line)
            if kind == Kind.CODE:
                code.append(line)
                continue
            if code:
                kept.append(kept_block(code, Kind.CODE))
                code = []
            if kind is not None:
                kept.append(kept_block([line], kind))
            else:
                lines.append(line)
        if code:
            kept.append(kept_block(code, Kind.CODE))
        if lines:
            groups.append((text_block, lines))

    rules = separating_rules([line for _, lines in groups for line in lines], content.drawings)
    blocks = [b for text_block, lines in groups for b in split_block(lines, text_block.bbox, rules)]
    translated = [b for b in blocks if b.action == Action.TRANSLATE]
    for block in translated:
        block.cell = cell_around(block, rules)
    align_in_cells(translated)
    classify(blocks)
    return blocks + kept


def _screens(blocks: list[list]) -> list[bool]:
    """Which text blocks reproduce a terminal or menu screen. A screen's
    next lines can be blocks of their own ("login:"), in any order."""
    screens = [is_screen(lines) for lines in blocks]
    grown = True
    while grown:
        grown = False
        for index, lines in enumerate(blocks):
            if not screens[index] and any(
                continues_screen(lines, other) for other, found in zip(blocks, screens) if found
            ):
                screens[index] = grown = True
    return screens
