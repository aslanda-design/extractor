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


class PageInfo(StrictModel):
    """Geometry and provenance of one page.

    Coordinates everywhere are PDF points (1/72 inch) with the origin at the
    TOP-LEFT of the page, y growing downward, so a bbox can be drawn on a
    rendered page image with a single scale factor.
    """

    page: int  # 1-based
    width: float
    height: float
    route: str = "native"  # "native" or "ocr": how the text of this page was obtained
    route_reason: str = ""  # which signals triggered the choice
    layout_coverage: float | None = None  # share of the PDF text layer's words the layout parse kept (0..1); None = not checked
    recovered_words: int = 0  # words the layout parse missed and that were restored from the text layer


class SourceBlock(StrictModel):
    block_id: str  # "{document_id}:p{page}:b{n}", n = reading order on the page
    document_id: str
    kind: BlockKind
    text: str
    page: int | None = None
    bbox: tuple[float, float, float, float] | None = None  # (left, top, right, bottom), see PageInfo
    heading_level: int | None = None  # for headings; hierarchy is built in phase 3
    heading_path: list[str] = []  # filled by phase 3
    parent_id: str | None = None  # filled by phase 3
    from_ocr: bool = False
    recovered: bool = False  # restored from the PDF text layer because the layout parse had dropped it
    parser_id: str | None = None  # id of the element in the raw parser output


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
    TEXT_NOT_COVERED = "text_not_covered"  # words of the PDF text layer absent from the layout parse


class ParsingIssue(StrictModel):
    kind: IssueKind
    document_id: str
    page: int | None = None
    bbox: tuple[float, float, float, float] | None = None  # region, when known
    message: str
    attempts: list[str] = []  # retry ladder steps tried, e.g. ["native", "ocr", "ocr_hires"]
    resolved: bool = False  # unresolved issues become coverage limitations


class DocumentRepresentation(StrictModel):
    document_id: str
    pages: list[PageInfo] = []
    raw_artifact: str | None = None  # artifact-store key of the raw parser output
    blocks: list[SourceBlock] = []
    tables: list[SourceTable] = []
    issues: list[ParsingIssue] = []
