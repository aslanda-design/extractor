"""S09 steps 3, 4 and 6 - From what the model reported to records with exact source spans.

  reconcile   a record seen in two windows is ONE record (same key and overlapping position); the same key in two different places is a
              duplicate key (kept apart and flagged). Similar text alone never merges records.
  build_spans a record starts where the model said it starts and ends where the next record starts, or at its last block. The span is
              made of pieces of blocks (a whole block, or part of one when several records share it), so a record that continues over a
              page break is simply the blocks between its start and its end.
  as_passage  the span as a `Passage`: the ONLY evidence its fields are read from, so text of one record can never fill another's field.
"""

import re
from dataclasses import dataclass, field

from ..contracts.evidence import EvidenceOrigin, EvidencePassage
from ..contracts.common import EvidenceRole
from ..contracts.passage import Passage, PassageKind, PassageSegment
from .discovery import Found
from .outline import Tagged, find_offset, is_noise, running_blocks


@dataclass
class Span:
    record_id: str
    key: str
    start: int
    offset: int
    end: int  # block index of the last block (inclusive)
    end_offset: int  # characters of the last block that belong to the record
    flags: list[str] = field(default_factory=list)
    text: str = ""
    block_ids: list[str] = field(default_factory=list)
    pieces: list[tuple[int, int, int]] = field(default_factory=list)  # (block index, from, to)

    def position(self) -> tuple[int, int]:
        return self.start, self.offset


def norm_key(key: str) -> str:
    return re.sub(r"[\s().,:;\[\]]+", "", key.casefold())


def separate_shared_starts(found: list[Found], tagged: list[Tagged]) -> None:
    """Several records the model put at the SAME position (it repeated one "starts_with" for a list of items inside a sentence) cannot all
    start there. Each such record is moved to where its own key appears in the block, when it does. Records whose key is not found stay put
    (and the audit keeps whatever ambiguity is left)."""
    by_position: dict[tuple[int, int], list[Found]] = {}
    for f in found:
        by_position.setdefault((f.start, f.offset), []).append(f)
    for (start, offset), group in by_position.items():
        if len(group) < 2 or len({norm_key(f.key) for f in group}) < 2:
            continue
        text = tagged[start].block.text
        for f in group:
            at = find_offset(text[offset:], f.key) if f.key else None
            if at is not None:
                f.offset = offset + at
                f.flags = [x for x in f.flags if x != "boundary_not_located"]


def reconcile(found: list[Found], tagged: list[Tagged] | None = None) -> tuple[list[Found], list[str]]:
    """Merge reports of the same record from overlapping windows; keep real duplicates apart. Returns (records, duplicate keys)."""
    if tagged is not None:
        separate_shared_starts(found, tagged)
    ordered = sorted(found, key=lambda f: (f.start, f.offset, f.window))
    merged: list[Found] = []
    duplicates: list[str] = []
    for f in ordered:
        same = [m for m in merged if norm_key(m.key) == norm_key(f.key) and norm_key(f.key)]
        twin = next((m for m in same if abs(m.start - f.start) <= 1), None)  # same key, same place: the window overlap saw it twice
        if twin is not None:  # the same record seen again (window overlap): keep the longer report
            twin.end = max(twin.end, f.end)
            twin.flags = sorted(set(twin.flags) | set(f.flags))
            continue
        if same:
            duplicates.append(f.key)
            f.flags = [*f.flags, "duplicate_key"]
        merged.append(f)
    return merged, sorted(set(duplicates))


def build_spans(document_id: str, task_id: str, found: list[Found], tagged: list[Tagged]) -> list[Span]:
    spans: list[Span] = []
    running = running_blocks(tagged)
    for n, f in enumerate(found):
        following = found[n + 1] if n + 1 < len(found) else None
        end, end_offset = f.end, len(tagged[f.end].block.text)
        if following is not None and (following.start, following.offset) > (f.start, f.offset) and following.start <= f.end:
            end, end_offset = following.start, following.offset  # the next record begins inside this one's last block
        if (end, end_offset) <= (f.start, f.offset):
            end, end_offset = f.start, len(tagged[f.start].block.text)
        span = Span(record_id=f"{task_id}@{tagged[f.start].block.block_id}+{f.offset}", key=f.key, start=f.start, offset=f.offset, end=end,
                    end_offset=end_offset, flags=list(f.flags))
        for i in range(f.start, end + 1):
            block = tagged[i].block
            a = f.offset if i == f.start else 0
            b = end_offset if i == end else len(block.text)
            if i not in (f.start, end) and (is_noise(block) or i in running):
                continue  # a printed page number or a running header in the middle of a record
            if block.text[a:b].strip():
                span.pieces.append((i, a, b))
        span.block_ids = [tagged[i].block.block_id for i, _, _ in span.pieces]
        span.text = "\n".join(tagged[i].block.text[a:b].strip() for i, a, b in span.pieces)
        spans.append(span)
    return spans


def as_passage(span: Span, document_id: str, tagged: list[Tagged]) -> Passage:
    """The span as a passage whose segments point at the exact pieces of source blocks (so a quote maps to real block ids).

    The first line is the record's KEY as discovery found it ("[record key: 12.1(2)]"): the label of a list item often needs the number of its
    parent clause, which is printed above the item and therefore not in the item's own text, while discovery (which sees the whole window)
    names the item by its full identity. The line is mapped to the record's first block, so a field read from it still cites real source."""
    header = f"[record key: {span.key}]" if span.key else ""
    text, segments, cursor = header, [], len(header)
    first = tagged[span.start].block
    if header:
        segments.append(PassageSegment(block_id=first.block_id, passage_start=0, passage_end=len(header), source_start=span.offset,
                                       source_end=min(len(first.text), span.offset + max(1, len(span.key)))))
    for i, a, b in span.pieces:
        block = tagged[i].block
        piece = block.text[a:b]
        lead = len(piece) - len(piece.lstrip())
        piece = piece.strip()
        if text:
            text += "\n"
            cursor += 1
        segments.append(PassageSegment(block_id=block.block_id, passage_start=cursor, passage_end=cursor + len(piece),
                                       source_start=a + lead, source_end=a + lead + len(piece)))
        text += piece
        cursor += len(piece)
    pages = sorted({tagged[i].block.page for i, _, _ in span.pieces if tagged[i].block.page})
    return Passage(passage_id=span.record_id, document_id=document_id, kind=PassageKind.TEXT, text=text, source_text=text,
                   heading_path=list(first.heading_path), segments=segments, token_count=max(1, len(text) // 4), pages=pages)


def as_evidence(passage: Passage) -> EvidencePassage:
    return EvidencePassage(
        passage_id=passage.passage_id, document_id=passage.document_id, text=passage.text, heading_path=passage.heading_path,
        pages=passage.pages, block_ids=[s.block_id for s in passage.segments], token_count=passage.token_count,
        origin=EvidenceOrigin.RETRIEVED, role=EvidenceRole.SUPPORTS, provenance=["record span"])
