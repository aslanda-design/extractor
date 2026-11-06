"""What the orchestrator needs from a parsing library. Implement these to swap one.

Everything crossing this boundary is already backend-neutral (raw.py types, PDF
points, top-left origin). Native objects of a library never leave its module.
"""

from pathlib import Path
from typing import Protocol

from ..raw import BBox, NativeExtraction, RawBlock


class NativeBackend(Protocol):
    """Reads the text layer / structure of a whole PDF in one pass."""

    name: str

    def extract(self, path: Path) -> NativeExtraction: ...


class OcrBackend(Protocol):
    """Runs layout + OCR on one rendered image (a full page or a crop)."""

    name: str

    def ocr_image(self, image_path: Path) -> list[RawBlock]:
        """Blocks with bboxes in IMAGE PIXELS, top-left origin."""


class PageRenderer(Protocol):
    """Page counting, geometry and rasterization of the original file."""

    def page_count(self, path: Path) -> int: ...

    def page_size(self, path: Path, page: int) -> tuple[float, float]:
        """(width, height) in points."""

    def render(self, path: Path, page: int, dpi: int, out: Path, crop: BBox | None = None) -> float:
        """Write a PNG of the page (or of `crop`, in points) and return pixels-per-point."""
