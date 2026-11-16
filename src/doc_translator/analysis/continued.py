"""Paragraphs that go on in another block: after a page break, or past a
running header, a page number or a figure.

Each piece translated on its own is half a sentence: the model guesses the
rest, repeats it or invents it ("cable into the RJ-45 receptacle..." began
a page, its subject on the page before). The pieces of one paragraph are
translated together and the translation is shared back between them.
"""

import re
from collections import Counter

from .model import Block, Kind

SENTENCE_END = re.compile(r"[.:;!?…]\s*[\"”’)\]]*\s*$")
# Blocks this short between two pieces are page furniture: running headers
# and footers, page numbers.
FURNITURE_WORDS = 4
# A piece this short is a label or a heading, not part of running text.
MIN_PIECE_WORDS = 5
# Formulas break lines anywhere; their pieces are not sentences.
FORMULA = re.compile(r"[=∑∏∫√∈≤≥]")


def continued(paragraphs: list[Block]) -> list[list[int]]:
    """Chains of indices into `paragraphs` (translated blocks, in reading
    order) that are pieces of one paragraph; each chain has 2+ pieces."""
    chains: list[list[int]] = []
    last: int | None = None  # the last piece of running text
    between: list[Block] = []
    for index, block in enumerate(paragraphs):
        if last is not None and _goes_on(paragraphs[last], block, between):
            if chains and chains[-1][-1] == last:
                chains[-1].append(index)
            else:
                chains.append([last, index])
            last, between = index, []
            continue
        if block.kind in (Kind.PARAGRAPH, Kind.LIST_ITEM) and len(block.text.split()) >= MIN_PIECE_WORDS:
            last, between = index, []
        else:
            between.append(block)
    return chains


def _goes_on(previous: Block, block: Block, between: list[Block]) -> bool:
    if block.kind != Kind.PARAGRAPH or not re.match(r"[a-z]", block.text.strip()):
        return False
    if SENTENCE_END.search(previous.text) or FORMULA.search(previous.text + block.text):
        return False
    if any(len(b.text.split()) > FURNITURE_WORDS for b in between):
        return False
    return _style(previous) == _style(block)


def _style(block: Block) -> tuple[str, int] | None:
    """The font and size most of the block's letters are set in."""
    counts: Counter[tuple[str, int]] = Counter()
    for span in block.spans:
        if span.text.strip():
            counts[(span.font, round(span.size))] += len(span.text)
    return counts.most_common(1)[0][0] if counts else None


def share(translation: str, lengths: list[int]) -> list[str]:
    """`translation` cut into len(lengths) pieces, in proportion to the
    pieces of the source, at spaces. A cut never passes its proportional
    point: an earlier piece ends a page, often with no room to spare, while
    the next one starts a page."""
    total = sum(lengths) or 1
    spaces = [m.start() for m in re.finditer(r"\s", translation)]
    pieces, start, done = [], 0, 0
    for length in lengths[:-1]:
        done += length
        target = len(translation) * done / total
        before = [s for s in spaces if start < s <= target]
        after = [s for s in spaces if s > start]
        cut = before[-1] if before else (after[0] if after else len(translation))
        pieces.append(translation[start:cut].strip())
        start = cut
    pieces.append(translation[start:].strip())
    return pieces
