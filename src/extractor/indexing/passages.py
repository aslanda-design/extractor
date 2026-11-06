"""S04 step 6 - Turn chunks into Passages and link them.

A Passage keeps, next to its normalized text, the original text and the exact
range of every source block it came from (`segments`), so any hit can be
traced back to the original file, page and position.
"""

from ..contracts.document import SourceBlock
from ..contracts.passage import Passage, PassageKind, PassageSegment
from .sizing import Chunk, Counter


def _segment(piece, start: int, end: int) -> PassageSegment:
    identity = list(range(piece.source_start, piece.source_end)) == piece.char_map
    return PassageSegment(
        block_id=piece.block_id, passage_start=start, passage_end=end,
        source_start=piece.source_start, source_end=piece.source_end,
        char_map=None if piece.line_granular or identity else list(piece.char_map))


def _dominant(chunk: Chunk):
    """The part carrying most text decides the passage's section (ties go to the later part)."""
    best = chunk.parts[0]
    for part in chunk.parts:
        if len(part.text()) >= len(best.text()):
            best = part
    return best.unit


def make_passage(document_id: str, number: int, chunk: Chunk, blocks: dict[str, SourceBlock], count: Counter) -> Passage:
    text, source_text = "", ""
    segments: list[PassageSegment] = []
    for p, part in enumerate(chunk.parts):
        for i, piece in enumerate(part.pieces):
            if not piece.norm_text:
                continue
            if text:
                joiner = "\n" if i == 0 else part.joiner
                text += joiner
                source_text += joiner
            start = len(text)
            text += piece.norm_text
            source_text += blocks[piece.block_id].text[piece.source_start:piece.source_end]
            segments.append(_segment(piece, start, len(text)))

    unit = _dominant(chunk)
    pages = sorted({blocks[s.block_id].page for s in segments if blocks[s.block_id].page is not None})
    return Passage(
        passage_id=f"{document_id}:c{number}",
        document_id=document_id,
        kind=PassageKind.TABLE if chunk.kind == "table" else PassageKind.TEXT,
        text=text, source_text=source_text, segments=segments,
        heading_path=list(unit.heading_path), section_block_id=unit.section_id,
        token_count=count(text), pages=pages,
        continued_across_pages=any(part.unit.continued for part in chunk.parts),
        note=chunk.note)


def link_neighbours(passages: list[Passage]) -> list[Passage]:
    """Set prev_id / next_id in reading order (no overlap: neighbours are expanded later, at S06)."""
    linked = []
    for i, p in enumerate(passages):
        linked.append(p.model_copy(update={
            "prev_id": passages[i - 1].passage_id if i > 0 else None,
            "next_id": passages[i + 1].passage_id if i + 1 < len(passages) else None}))
    return linked
