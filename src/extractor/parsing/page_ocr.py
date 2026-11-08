"""S03 - OCR helpers: run OCR on a page or a crop, and the OCR retry ladder.

Used by parsing_pipeline.py. Nothing here imports a parsing library (backends are injected).
"""

import tempfile
from pathlib import Path

from ..config.runtime import ParsingConfig
from .backends.base import OcrBackend, PageRenderer
from .geometry import scale
from .raw import BBox, RawBlock, RawPage
from .routing import compute_signals


class PageOcr:
    """OCR of whole pages / crops, with pixel -> point mapping."""

    def __init__(self, ocr: OcrBackend, renderer: PageRenderer, config: ParsingConfig):
        self.ocr, self.renderer, self.config = ocr, renderer, config

    def run(self, path: Path, page: int, dpi: int, crop: BBox | None = None) -> list[RawBlock]:
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "page.png"
            pixels_per_point = self.renderer.render(path, page, dpi, image, crop)
            blocks = self.ocr.ocr_image(image)
        dx, dy = (crop[0], crop[1]) if crop else (0.0, 0.0)
        out = []
        for b in blocks:
            bbox = scale(b.bbox, 1 / pixels_per_point, dx, dy) if b.bbox else None
            out.append(b.model_copy(update={"bbox": bbox, "from_ocr": True}))
        return out


def ocr_page(
    page_ocr: PageOcr, path: Path, number: int, size: tuple[float, float], config: ParsingConfig
) -> tuple[RawPage, list[str]]:
    """OCR ladder for a whole page (ocr_dpi, then ocr_hires_dpi). Returns the best page and the rungs tried."""
    attempts: list[str] = []
    best = RawPage(page=number, width=size[0], height=size[1])
    for rung, dpi in (("ocr", config.ocr_dpi), ("ocr_hires", config.ocr_hires_dpi)):
        attempts.append(rung)
        try:
            blocks = page_ocr.run(path, number, dpi)
        except Exception as exc:  # backend failure: record and climb the ladder
            attempts[-1] = f"{rung}(error: {exc})"
            continue
        best = RawPage(page=number, width=size[0], height=size[1], blocks=blocks)
        if compute_signals(best).chars >= config.min_native_chars:
            break
    return best, attempts
