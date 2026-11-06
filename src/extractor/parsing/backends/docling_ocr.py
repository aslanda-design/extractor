"""OcrBackend using Docling: layout analysis + OCR + table structure on one image.

The only module that imports Docling. Models are cached under `models_dir`
(inside the artifact store) so runs work offline after the first download.
Output is translated to RawBlocks in image pixels with a top-left origin.
"""

from pathlib import Path

from ...contracts.document import BlockKind, TableCell
from ..raw import RawBlock

# Docling label -> our block kind. Labels not listed are ignored (pictures, etc.).
_KINDS = {
    "title": BlockKind.HEADING,
    "section_header": BlockKind.HEADING,
    "list_item": BlockKind.LIST_ITEM,
    "text": BlockKind.PARAGRAPH,
    "paragraph": BlockKind.PARAGRAPH,
    "caption": BlockKind.PARAGRAPH,
    "footnote": BlockKind.OTHER,
    "page_header": BlockKind.OTHER,
    "page_footer": BlockKind.OTHER,
    "code": BlockKind.OTHER,
    "formula": BlockKind.OTHER,
    "checkbox_selected": BlockKind.OTHER,
    "checkbox_unselected": BlockKind.OTHER,
    "reference": BlockKind.OTHER,
}


class DoclingOcrBackend:
    name = "docling"

    def __init__(self, languages: list[str] | None = None, device: str = "auto", models_dir: Path | None = None):
        self.languages = languages or ["en"]
        self.device = device
        self.models_dir = Path(models_dir) if models_dir else None
        self._converter = None

    # --- lazy setup (loading models is slow, do it once) ------------------------

    def _build_converter(self):
        from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions
        from docling.document_converter import DocumentConverter, ImageFormatOption

        artifacts = None
        if self.models_dir is not None:
            if not (self.models_dir / "RapidOcr").exists():
                from docling.utils.model_downloader import download_models

                self.models_dir.mkdir(parents=True, exist_ok=True)
                download_models(output_dir=self.models_dir, with_code_formula=False,
                                with_picture_classifier=False, with_rapidocr=True)
            artifacts = str(self.models_dir)

        device = {"auto": AcceleratorDevice.AUTO, "cuda": AcceleratorDevice.CUDA,
                  "cpu": AcceleratorDevice.CPU}[self.device]
        options = PdfPipelineOptions(
            do_ocr=True,
            do_table_structure=True,
            artifacts_path=artifacts,
            accelerator_options=AcceleratorOptions(device=device),
            ocr_options=RapidOcrOptions(backend="torch", force_full_page_ocr=True),
        )
        return DocumentConverter(format_options={InputFormat.IMAGE: ImageFormatOption(pipeline_options=options)})

    @property
    def converter(self):
        if self._converter is None:
            self._converter = self._build_converter()
        return self._converter

    # --- OcrBackend ------------------------------------------------------------

    def ocr_image(self, image_path: Path) -> list[RawBlock]:
        doc = self.converter.convert(str(image_path)).document
        blocks: list[RawBlock] = []
        for item, _level in doc.iterate_items():
            label = getattr(item.label, "value", str(item.label))
            if not item.prov:
                continue
            prov = item.prov[0]
            page = doc.pages[prov.page_no]
            bbox = prov.bbox.to_top_left_origin(page_height=page.size.height).as_tuple()
            if label == "table":
                blocks.append(self._table_block(item, bbox))
            elif label in _KINDS and getattr(item, "text", "").strip():
                level = getattr(item, "level", None)
                kind = _KINDS[label]
                blocks.append(RawBlock(
                    kind=kind, text=item.text, bbox=tuple(bbox),
                    heading_level=(level or 1) if kind == BlockKind.HEADING else None,
                    parser_id=getattr(item, "self_ref", None)))
        return blocks

    @staticmethod
    def _table_block(item, bbox) -> RawBlock:
        cells = [
            TableCell(row=c.start_row_offset_idx, col=c.start_col_offset_idx, text=c.text,
                      is_header=bool(c.column_header or c.row_header))
            for c in item.data.table_cells
        ]
        rows: dict[int, list[str]] = {}
        for c in sorted(cells, key=lambda c: (c.row, c.col)):
            rows.setdefault(c.row, []).append(c.text)
        text = "\n".join("\t".join(r) for r in rows.values())
        return RawBlock(kind=BlockKind.TABLE, text=text, bbox=tuple(bbox), cells=cells,
                        parser_id=getattr(item, "self_ref", None))
