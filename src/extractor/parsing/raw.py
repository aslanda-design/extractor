"""Backend-neutral intermediate types for S03.

Backends (OpenDataLoader, Docling) translate their native output into these,
always in PDF points with a TOP-LEFT origin (see contracts.document.PageInfo).
`normalize.py` then turns them into the public `SourceBlock`s.
"""

from ..contracts.common import StrictModel
from ..contracts.document import BlockKind, TableCell

BBox = tuple[float, float, float, float]  # (left, top, right, bottom)


class RawBlock(StrictModel):
    kind: BlockKind
    text: str
    bbox: BBox | None = None
    parser_id: str | None = None
    heading_level: int | None = None
    cells: list[TableCell] = []  # only for tables
    from_ocr: bool = False
    recovered: bool = False  # restored from the PDF text layer by reconcile.py


class RefWord(StrictModel):
    """One word of the PDF's own text layer (the independent reference used to check completeness)."""

    text: str
    bbox: BBox


class RawPage(StrictModel):
    page: int  # 1-based
    width: float
    height: float
    blocks: list[RawBlock] = []
    image_boxes: list[BBox] = []  # pictures / scanned areas, used by routing signals


class NativeExtraction(StrictModel):
    """Everything one native pass produced for a whole document."""

    pages: list[RawPage]
    raw: dict | list  # untouched parser output, stored in the artifact store
