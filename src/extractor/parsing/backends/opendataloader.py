"""NativeBackend using OpenDataLoader PDF (Java CLI wrapped by a Python package).

The only module that imports it. Its JSON uses PDF coordinates with a
BOTTOM-left origin: [left, bottom, right, top]; we convert to top-left
(left, top, right, bottom). The JSON has no page sizes, so they come from pdfium.

`include_header_footer=True` is deliberate: by default the tool drops
header/footer regions, which would silently discard content (a title near the
top of a page was dropped in testing). Nothing is thrown away at parse time.
"""

import json
import tempfile
from pathlib import Path

from ...contracts.document import BlockKind, TableCell
from ..raw import BBox, NativeExtraction, RawBlock, RawPage

_CONTAINERS = {"header", "footer", "list", "text block", "section", "div"}
_TEXT_KINDS = {
    "heading": BlockKind.HEADING,
    "paragraph": BlockKind.PARAGRAPH,
    "caption": BlockKind.PARAGRAPH,
    "list item": BlockKind.LIST_ITEM,
}


def _to_top_left(bbox: list[float], page_height: float) -> BBox:
    left, bottom, right, top = bbox
    return (left, page_height - top, right, page_height - bottom)


class OpenDataLoaderBackend:
    name = "opendataloader"

    def __init__(self, renderer=None):
        if renderer is None:
            from .pdfium_renderer import PdfiumRenderer

            renderer = PdfiumRenderer()
        self.renderer = renderer

    def extract(self, path: Path) -> NativeExtraction:
        import opendataloader_pdf

        with tempfile.TemporaryDirectory() as tmp:
            opendataloader_pdf.convert(
                str(path), output_dir=tmp, format="json", quiet=True, include_header_footer=True
            )
            produced = next(Path(tmp).glob("*.json"))
            raw = json.loads(produced.read_text(encoding="utf-8"))

        n_pages = int(raw.get("number of pages") or self.renderer.page_count(path))
        pages = {}
        for number in range(1, n_pages + 1):  # every page exists, even if it has no elements
            width, height = self.renderer.page_size(path, number)
            pages[number] = RawPage(page=number, width=width, height=height)

        for element in raw.get("kids", []):
            self._walk(element, pages)
        return NativeExtraction(pages=list(pages.values()), raw=raw)

    # --- translation -----------------------------------------------------------

    def _walk(self, element: dict, pages: dict[int, RawPage]) -> None:
        kind = element.get("type", "")
        page = pages.get(element.get("page number"))
        if kind == "image":
            if page and element.get("bounding box"):
                page.image_boxes.append(_to_top_left(element["bounding box"], page.height))
        elif kind == "table":
            if page:
                page.blocks.append(self._table(element, page))
        elif kind in _TEXT_KINDS and "content" in element:
            if page:
                bbox = _to_top_left(element["bounding box"], page.height) if element.get("bounding box") else None
                page.blocks.append(RawBlock(
                    kind=_TEXT_KINDS[kind], text=element["content"], bbox=bbox,
                    parser_id=str(element.get("id")),
                    heading_level=element.get("heading level") if kind == "heading" else None))
        else:  # containers (header, footer, list, ...) and unknown types: descend
            for child_key in ("kids", "list items"):
                for child in element.get(child_key, []):
                    self._walk(child, pages)
            if kind not in _CONTAINERS and "content" in element and page:  # unknown text-bearing type
                bbox = _to_top_left(element["bounding box"], page.height) if element.get("bounding box") else None
                page.blocks.append(RawBlock(kind=BlockKind.OTHER, text=element["content"], bbox=bbox,
                                            parser_id=str(element.get("id"))))

    @staticmethod
    def _cell_text(cell: dict) -> str:
        return " ".join(k.get("content", "") for k in cell.get("kids", []) if k.get("content")).strip()

    def _table(self, element: dict, page: RawPage) -> RawBlock:
        cells: list[TableCell] = []
        for row in element.get("rows", []):
            for cell in row.get("cells", []):
                cells.append(TableCell(
                    row=cell["row number"] - 1, col=cell["column number"] - 1,
                    text=self._cell_text(cell), is_header=bool(cell.get("is_header"))))
        lines: dict[int, list[str]] = {}
        for c in sorted(cells, key=lambda c: (c.row, c.col)):
            lines.setdefault(c.row, []).append(c.text)
        bbox = _to_top_left(element["bounding box"], page.height) if element.get("bounding box") else None
        return RawBlock(kind=BlockKind.TABLE, text="\n".join("\t".join(r) for r in lines.values()),
                        bbox=bbox, cells=cells, parser_id=str(element.get("id")))
