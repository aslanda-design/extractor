"""S04 step 5 - Group units by section, then apply the size rules.

    units --group_into_sections--> groups (one per section stretch; tables alone)
          --merge_small_groups---> tiny groups join a neighbour
          --cut_into_chunks------> every chunk <= max_tokens

A Chunk is what becomes one Passage. Size is always measured with the
`count` function (the embedding model's tokenizer), on the normalized text.
Splitting never cuts below a sentence unless one sentence alone is too long,
and then only at word boundaries.
"""

import re
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from ..config.runtime import ChunkingConfig
from .units import Piece, Unit

Counter = Callable[[str], int]
_SENTENCE_END = re.compile(r"(?<=[.!?;:])\s+")
MIN_USEFUL_ROOM = 16  # tokens; below this a heading is not worth sharing a chunk with split text


@dataclass
class Part:
    """Pieces of one unit that travel together into a chunk."""

    unit: Unit
    pieces: list[Piece]

    @property
    def joiner(self) -> str:
        return "\n" if self.unit.kind == "table" else " "

    def text(self) -> str:
        return self.joiner.join(p.norm_text for p in self.pieces if p.norm_text)


@dataclass
class Group:
    parts: list[Part]
    kind: str  # "text" | "table"
    tokens: int = 0

    @property
    def heading_only(self) -> bool:
        return all(p.unit.kind == "heading" for p in self.parts)


@dataclass
class Chunk:
    parts: list[Part]
    kind: str
    note: str = ""
    extra: dict = field(default_factory=dict)


# --- grouping ---------------------------------------------------------------------


def _tokens(parts: list[Part], count: Counter) -> int:
    return count("\n".join(p.text() for p in parts)) if parts else 0


def group_into_sections(units: list[Unit], count: Counter) -> list[Group]:
    """Contiguous units of the same section form a group; every table is a group of its own."""
    groups: list[Group] = []
    current: Group | None = None
    section = object()
    for unit in units:
        part = Part(unit, unit.pieces)
        if unit.kind == "table":
            current = None
            groups.append(Group([part], "table"))
        elif current is None or unit.section_id != section:
            current = Group([part], "text")
            section = unit.section_id
            groups.append(current)
        else:
            current.parts.append(part)
    for g in groups:
        g.tokens = _tokens(g.parts, count)
    return groups


def merge_small_groups(groups: list[Group], config: ChunkingConfig, count: Counter) -> list[Group]:
    """Join text groups below min_tokens to a neighbouring text group (never into a table).

    A heading-only group belongs with what follows it, so it merges FORWARD
    first (even past max_tokens: the cut step keeps heading and text together);
    other small groups merge BACKWARD first. Those merges are skipped when the
    result would exceed max_tokens.
    """
    groups = list(groups)
    changed = True
    while changed:
        changed = False
        # lone headings first, so no other merge can leave one dangling at the end of a chunk
        for i, g in sorted(enumerate(groups), key=lambda item: not item[1].heading_only):
            if g.kind != "text" or g.tokens >= config.min_tokens:
                continue
            prev = groups[i - 1] if i > 0 and groups[i - 1].kind == "text" else None
            nxt = groups[i + 1] if i + 1 < len(groups) and groups[i + 1].kind == "text" else None
            order = (nxt, prev) if g.heading_only else (prev, nxt)
            for neighbour in order:
                if neighbour is None:
                    continue
                first, second = (g, neighbour) if neighbour is nxt else (neighbour, g)
                # A lone heading always joins the text it introduces; cutting later keeps them together.
                forced = g.heading_only and neighbour is nxt
                if forced or _tokens(first.parts + second.parts, count) <= config.max_tokens:
                    merged = Group(first.parts + second.parts, "text")
                    merged.tokens = _tokens(merged.parts, count)
                    lo = min(groups.index(first), groups.index(second))
                    groups[lo:lo + 2] = [merged]
                    changed = True
                    break
            if changed:
                break
    return groups


# --- splitting ----------------------------------------------------------------------


def _slice_piece(piece: Piece, a: int, b: int) -> Piece:
    """Sub-piece covering normalized text [a:b] of `piece` (text pieces only)."""
    while a < b and piece.norm_text[a].isspace():
        a += 1
    while b > a and piece.norm_text[b - 1].isspace():
        b -= 1
    mapped = piece.char_map[a:b]
    start, end = mapped[0], mapped[-1] + 1
    sub = Piece(piece.block_id, piece.text[start - piece.source_start:end - piece.source_start], start, end)
    sub.norm_text, sub.char_map = piece.norm_text[a:b], mapped
    return sub


def split_piece(piece: Piece, max_tokens: int, count: Counter) -> list[Piece]:
    """Cut one long text piece at sentence boundaries (words if a sentence alone is too long)."""
    text = piece.norm_text
    if count(text) <= max_tokens or piece.line_granular:
        return [piece]
    # sentence spans
    bounds, last = [], 0
    for m in _SENTENCE_END.finditer(text):
        bounds.append((last, m.start()))
        last = m.end()
    bounds.append((last, len(text)))
    # break sentences that are too long on their own into words
    spans: list[tuple[int, int]] = []
    for a, b in bounds:
        if count(text[a:b]) <= max_tokens:
            spans.append((a, b))
            continue
        words = [(a + m.start(), a + m.end()) for m in re.finditer(r"\S+", text[a:b])]
        start = end = words[0][0]
        for word_start, word_end in words:
            if end > start and count(text[start:word_end]) > max_tokens:
                spans.append((start, end))
                start = word_start
            end = word_end
        spans.append((start, end))
    # pack spans greedily
    out: list[Piece] = []
    a = spans[0][0]
    end = a
    for s, e in spans:
        if end > a and count(text[a:e]) > max_tokens:
            out.append(_slice_piece(piece, a, end))
            a = s
        end = e
    out.append(_slice_piece(piece, a, end))
    return out


def _split_parts(parts: list[Part], max_tokens: int, count: Counter) -> list[Part]:
    out: list[Part] = []
    for part in parts:
        if part.unit.kind == "table" or count(part.text()) <= max_tokens:
            out.append(part)
            continue
        for piece in part.pieces:
            out += [Part(part.unit, [sub]) for sub in split_piece(piece, max_tokens, count)]
    return out


def _cut_text_group(group: Group, max_tokens: int, count: Counter) -> list[Chunk]:
    queue = deque(_split_parts(group.parts, max_tokens, count))
    chunks: list[list[Part]] = [[]]
    while queue:
        part = queue.popleft()
        current = chunks[-1]
        if not current or _tokens(current + [part], count) <= max_tokens:
            current.append(part)
            continue
        # Does not fit. If the chunk holds only headings, fill the remaining room with
        # the start of this text instead of leaving the headings alone.
        budget = max_tokens - _tokens(current, count)
        if all(p.unit.kind == "heading" for p in current) and part.unit.kind != "heading" and budget >= MIN_USEFUL_ROOM:
            pieces = [s for piece in part.pieces for s in split_piece(piece, budget, count)]
            if len(pieces) > 1:
                queue.extendleft(reversed([Part(part.unit, [s]) for s in pieces]))
                continue
        chunks.append([part])
    # a heading must stay with the text it introduces, not end a chunk
    for i in range(len(chunks) - 1):
        while len(chunks[i]) > 1 and chunks[i][-1].unit.kind == "heading":
            chunks[i + 1].insert(0, chunks[i].pop())
    return [Chunk(c, "text") for c in chunks if c]


def _cut_table_group(group: Group, max_tokens: int, count: Counter) -> list[Chunk]:
    part = group.parts[0]
    pieces, n_header = part.pieces, part.unit.header_rows
    header, body = pieces[:n_header], pieces[n_header:]
    if group.tokens <= max_tokens or not body:
        return [Chunk([part], "table")]
    chunks: list[Chunk] = []
    rows: list[Piece] = []
    first_row = n_header + 1

    def flush(last_row: int) -> None:
        sub = Part(part.unit, header + rows)
        note = f"table split: rows {first_row}-{last_row} of {len(pieces)}"
        chunks.append(Chunk([sub], "table", note + (", header repeated" if header else "")))

    for i, row in enumerate(body, start=n_header + 1):
        trial = Part(part.unit, header + rows + [row])
        if rows and count(trial.text()) > max_tokens:
            flush(i - 1)
            rows, first_row = [], i
        rows.append(row)
    flush(len(pieces))
    return chunks


def cut_into_chunks(groups: list[Group], config: ChunkingConfig, count: Counter) -> list[Chunk]:
    chunks: list[Chunk] = []
    for group in groups:
        if group.kind == "table":
            chunks += _cut_table_group(group, config.max_tokens, count)
        else:
            chunks += _cut_text_group(group, config.max_tokens, count)
    return chunks
