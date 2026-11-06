"""S04 steps 3-4 - Units: the smallest things passages are made of.

A unit is one heading, one paragraph (or list item), or one table, after
cross-page continuations have been joined:

    paragraph continued on the next page  -> ONE unit with two pieces
    table continued on the next page      -> ONE unit with the rows of both

A piece is a slice of ONE source block's text, with its place in that block.
`normalize_units` turns pieces into search-view text with a map back to the
source characters (see search_text.py).
"""

from dataclasses import dataclass, field

from ..config.runtime import ChunkingConfig
from ..contracts.document import BlockKind, SourceBlock, SourceTable
from ..contracts.passage import Continuation
from .search_text import normalize_with_map


@dataclass
class Piece:
    block_id: str
    text: str  # source text of this piece (for tables: the row cells joined with " | ")
    source_start: int
    source_end: int
    line_granular: bool = False  # table rows map to a whole line, not character by character
    # filled by normalize_units
    norm_text: str = ""
    char_map: list[int] = field(default_factory=list)


@dataclass
class Unit:
    kind: str  # "heading" | "text" | "table"
    pieces: list[Piece]
    section_id: str | None  # heading block this unit sits under (a heading is its own section)
    heading_path: list[str]
    pages: list[int]
    continued: bool = False  # built from blocks on several pages
    header_rows: int = 0  # tables: how many leading pieces are header rows

    @property
    def block_ids(self) -> list[str]:
        return list(dict.fromkeys(p.block_id for p in self.pieces))

    def norm_text(self, joiner: str = " ") -> str:
        return joiner.join(p.norm_text for p in self.pieces if p.norm_text)


# --- building units -------------------------------------------------------------


def _table_rows(block: SourceBlock, table: SourceTable | None) -> tuple[list[Piece], list[bool], list[tuple[str, ...]]]:
    """Rows of one table block as pieces, header flags, and cell-text tuples."""
    lines = block.text.split("\n")
    offsets, pos = [], 0
    for line in lines:
        offsets.append((pos, pos + len(line)))
        pos += len(line) + 1
    pieces, headers, cells_per_row = [], [], []
    if table is None or not table.cells:
        return [Piece(block.block_id, block.text, 0, len(block.text), True)], [False], [(block.text,)]
    rows: dict[int, list] = {}
    for cell in sorted(table.cells, key=lambda c: (c.row, c.col)):
        rows.setdefault(cell.row, []).append(cell)
    for line_no, (_, cells) in enumerate(sorted(rows.items())):
        start, end = offsets[line_no] if line_no < len(offsets) else (0, len(block.text))
        texts = tuple(c.text for c in cells)
        pieces.append(Piece(block.block_id, " | ".join(texts), start, end, True))
        headers.append(any(c.is_header for c in cells))
        cells_per_row.append(texts)
    return pieces, headers, cells_per_row


def build_units(
    blocks: list[SourceBlock],
    tables: list[SourceTable],
    continuations: list[Continuation],
    config: ChunkingConfig,
) -> list[Unit]:
    """Reading-order units. `blocks` must already carry heading_path / parent_id."""
    by_id = {b.block_id: b for b in blocks}
    table_by_id = {t.block_id: t for t in tables}
    next_of = {c.from_block_id: c.to_block_id for c in continuations} if config.link_continuations else {}
    continues_something = set(next_of.values())

    units: list[Unit] = []
    for block in blocks:
        if block.block_id in continues_something or not block.text.strip():
            continue  # absorbed into an earlier unit, or nothing to index
        chain = [block]
        while chain[-1].block_id in next_of:
            chain.append(by_id[next_of[chain[-1].block_id]])
        section = block.block_id if block.kind == BlockKind.HEADING else block.parent_id
        common = dict(section_id=section, heading_path=list(block.heading_path),
                      pages=sorted({b.page for b in chain if b.page is not None}), continued=len(chain) > 1)

        if block.kind == BlockKind.TABLE:
            pieces: list[Piece] = []
            header_flags: list[bool] = []
            first_header: tuple[str, ...] | None = None
            for part in chain:
                rows, flags, texts = _table_rows(part, table_by_id.get(part.block_id))
                if pieces and flags and flags[0] and texts[0] == first_header:
                    rows, flags = rows[1:], flags[1:]  # repeated header row on the next page
                if first_header is None and texts:
                    first_header = texts[0]
                pieces += rows
                header_flags += flags
            header_rows = 0
            for flag in header_flags:
                if not flag:
                    break
                header_rows += 1
            if header_rows == 0 and config.table_first_row_is_header and len(pieces) >= 2:
                header_rows = 1
            units.append(Unit(kind="table", pieces=pieces, header_rows=header_rows, **common))
        else:
            pieces = [Piece(b.block_id, b.text, 0, len(b.text)) for b in chain]
            kind = "heading" if block.kind == BlockKind.HEADING else "text"
            units.append(Unit(kind=kind, pieces=pieces, **common))
    return units


def normalize_units(units: list[Unit]) -> None:
    """Fill every piece's search-view text and char map (in place)."""
    for unit in units:
        for piece in unit.pieces:
            piece.norm_text, piece.char_map = normalize_with_map(piece.text)
