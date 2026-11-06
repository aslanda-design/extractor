"""S03 - Turn backend-neutral RawPages into the public DocumentRepresentation parts.

Assigns the stable block ids `{document_id}:p{page}:b{n}` (n = reading order on
the page, starting at 1) and keeps the parser's own id for traceability.
"""

from ..contracts.document import PageInfo, SourceBlock, SourceTable
from .raw import RawBlock, RawPage


def block_id(document_id: str, page: int, index: int) -> str:
    return f"{document_id}:p{page}:b{index}"


def to_source_blocks(
    document_id: str, page: RawPage, from_ocr: bool = False
) -> tuple[list[SourceBlock], list[SourceTable]]:
    blocks: list[SourceBlock] = []
    tables: list[SourceTable] = []
    index = 0
    for raw in page.blocks:
        if not raw.text.strip() and not raw.cells:
            continue  # nothing to cite
        index += 1
        bid = block_id(document_id, page.page, index)
        blocks.append(
            SourceBlock(
                block_id=bid,
                document_id=document_id,
                kind=raw.kind,
                text=raw.text,
                page=page.page,
                bbox=raw.bbox,
                heading_level=raw.heading_level,
                from_ocr=from_ocr or raw.from_ocr,
                parser_id=raw.parser_id,
            )
        )
        if raw.cells:
            tables.append(SourceTable(block_id=bid, cells=raw.cells))
    return blocks, tables


def page_info(page: RawPage, route: str, reason: str) -> PageInfo:
    return PageInfo(page=page.page, width=page.width, height=page.height, route=route, route_reason=reason)
