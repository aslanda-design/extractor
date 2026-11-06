"""Map text of the search view back to the original source (and check the mapping).

`source_ranges(passage, start, end)` answers: "this part of the passage text
came from which characters of which source blocks?" Combined with each
block's page and bounding box, it locates evidence on the original file.
"""

from ..contracts.document import DocumentRepresentation
from ..contracts.passage import IndexedDocument, Passage


def source_ranges(passage: Passage, start: int, end: int) -> list[tuple[str, int, int]]:
    """(block_id, source_start, source_end) for passage text [start:end).

    Table rows are line-granular: the whole row line is returned.
    """
    ranges = []
    for seg in passage.segments:
        lo, hi = max(start, seg.passage_start), min(end, seg.passage_end)
        if lo >= hi:
            continue
        if seg.char_map is None and seg.source_end - seg.source_start == seg.passage_end - seg.passage_start:
            offset = seg.source_start - seg.passage_start
            ranges.append((seg.block_id, lo + offset, hi + offset))
        elif seg.char_map is None:  # table row: line-granular
            ranges.append((seg.block_id, seg.source_start, seg.source_end))
        else:
            mapped = seg.char_map[lo - seg.passage_start:hi - seg.passage_start]
            ranges.append((seg.block_id, mapped[0], mapped[-1] + 1))
    return ranges


def check_invariants(doc: IndexedDocument) -> list[str]:
    """Structural promises of S04. Returns human-readable violations (empty = healthy)."""
    problems: list[str] = []
    rep: DocumentRepresentation = doc.representation
    blocks = {b.block_id: b for b in rep.blocks}
    ids = [p.passage_id for p in doc.passages]
    if len(set(ids)) != len(ids):
        problems.append("duplicate passage ids")

    covered: set[str] = set()
    for i, p in enumerate(doc.passages):
        if not p.segments:
            problems.append(f"{p.passage_id}: no segments")
        for seg in p.segments:
            block = blocks.get(seg.block_id)
            if block is None:
                problems.append(f"{p.passage_id}: unknown block {seg.block_id}")
                continue
            covered.add(seg.block_id)
            if not (0 <= seg.passage_start < seg.passage_end <= len(p.text)):
                problems.append(f"{p.passage_id}: bad passage range in segment of {seg.block_id}")
            if not (0 <= seg.source_start < seg.source_end <= len(block.text)):
                problems.append(f"{p.passage_id}: bad source range in segment of {seg.block_id}")
            if seg.char_map is not None and len(seg.char_map) != seg.passage_end - seg.passage_start:
                problems.append(f"{p.passage_id}: char_map length mismatch in {seg.block_id}")
        if p.prev_id != (ids[i - 1] if i > 0 else None) or p.next_id != (ids[i + 1] if i + 1 < len(ids) else None):
            problems.append(f"{p.passage_id}: neighbour links inconsistent")

    for b in rep.blocks:
        if b.text.strip() and b.block_id not in covered:
            problems.append(f"content dropped: block {b.block_id} is in no passage")
    return problems
