"""DocumentRepresentation: the shared parsed view of a document (stages S03/S04).

Phase 0 defines the shape only; the parser (phase 2) fills it in.
"""

from enum import StrEnum

from .common import StrictModel


class BlockKind(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    TABLE = "table"
    FIGURE = "figure"
    OTHER = "other"


class SourceBlock(StrictModel):
    block_id: str  # stable, referenced by all evidence
    document_id: str
    kind: BlockKind
    text: str
    page: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    heading_path: list[str] = []
    parent_id: str | None = None
    from_ocr: bool = False


class TableCell(StrictModel):
    row: int
    col: int
    text: str
    is_header: bool = False


class SourceTable(StrictModel):
    block_id: str
    cells: list[TableCell]


class IssueKind(StrEnum):
    MISSING_PAGE = "missing_page"
    EMPTY_PAGE = "empty_page"
    CORRUPTED_TEXT = "corrupted_text"
    BROKEN_READING_ORDER = "broken_reading_order"
    INCOMPLETE_TABLE = "incomplete_table"
    OCR_FAILED = "ocr_failed"
    NOT_PARSED = "not_parsed"


class ParsingIssue(StrictModel):
    kind: IssueKind
    document_id: str
    page: int | None = None
    message: str
    resolved: bool = False  # unresolved issues become coverage limitations


class DocumentRepresentation(StrictModel):
    document_id: str
    blocks: list[SourceBlock] = []
    tables: list[SourceTable] = []
    issues: list[ParsingIssue] = []
