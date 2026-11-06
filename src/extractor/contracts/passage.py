"""Passages, links and the indexed document (stage S04).

Two linked views of one document:
  source view  DocumentRepresentation: original blocks, tables, positions
  search view  Passage: normalized text cut along document structure

Every passage maps back to the source view through `segments`.
"""

from enum import StrEnum

from .common import StrictModel
from .coverage import CoverageLedger
from .document import DocumentRepresentation


class PassageKind(StrEnum):
    TEXT = "text"
    TABLE = "table"


class PassageSegment(StrictModel):
    """One block's contribution to a passage, with its place in both views.

    `passage_start:passage_end` is a range in `Passage.text`;
    `source_start:source_end` is the matching range in the source block's text.
    `char_map[i]` is the source offset of passage character `passage_start + i`
    when normalization changed offsets; None means the ranges are 1:1.
    Table rows are line-granular: the range covers the whole row line.
    """

    block_id: str
    passage_start: int
    passage_end: int
    source_start: int
    source_end: int
    char_map: list[int] | None = None


class Passage(StrictModel):
    passage_id: str  # "{document_id}:c{n}", n = reading order
    document_id: str
    kind: PassageKind = PassageKind.TEXT
    text: str  # normalized search text (body only)
    source_text: str  # the same content before normalization
    heading_path: list[str] = []  # section context, outermost first
    section_block_id: str | None = None  # heading block of the section it mostly belongs to
    segments: list[PassageSegment]
    token_count: int
    pages: list[int] = []
    prev_id: str | None = None
    next_id: str | None = None
    continued_across_pages: bool = False
    note: str = ""  # e.g. "table split: rows 4-9, header repeated"

    def indexed_text(self, include_heading: bool = True) -> str:
        """What lexical and embedding indexes see: heading context + body."""
        if include_heading and self.heading_path:
            return " > ".join(self.heading_path) + "\n" + self.text
        return self.text


class ContinuationKind(StrEnum):
    PARAGRAPH = "paragraph"
    TABLE = "table"


class Continuation(StrictModel):
    """`to_block_id` continues `from_block_id` on the next page."""

    from_block_id: str
    to_block_id: str
    kind: ContinuationKind


class Reference(StrictModel):
    """An explicit pointer such as "see Section 5.2" found in a block."""

    source_block_id: str
    text: str  # as written, e.g. "Section 5.2"
    label: str  # normalized lookup key, e.g. "section 5.2"
    target_block_id: str | None = None
    resolved: bool = False


class IndexedDocument(StrictModel):
    """Everything S04 produces for one document (cached as one artifact)."""

    document_id: str
    representation: DocumentRepresentation  # blocks now carry heading_path / parent_id
    passages: list[Passage]
    continuations: list[Continuation] = []
    references: list[Reference] = []
    coverage: CoverageLedger = CoverageLedger()
    index_key: str = ""  # collection name in the lexical / vector stores
    embedding_model: str = ""  # identity label of the model used for the vectors

    def passage(self, passage_id: str) -> Passage:
        for p in self.passages:
            if p.passage_id == passage_id:
                return p
        raise KeyError(passage_id)
