"""PageRenderer using pypdfium2 (PDFs) and Pillow (image files).

Image files are treated as a one-page document at 72 dpi, so their size in
"points" equals their size in pixels and OCR output needs no extra scaling.
"""

from pathlib import Path

from ..raw import BBox

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}


def _is_image(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_SUFFIXES


class PdfiumRenderer:
    def page_count(self, path: Path) -> int:
        if _is_image(path):
            return 1
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(str(path))
        try:
            return len(pdf)
        finally:
            pdf.close()

    def page_size(self, path: Path, page: int) -> tuple[float, float]:
        if _is_image(path):
            from PIL import Image

            with Image.open(path) as im:
                return float(im.width), float(im.height)
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(str(path))
        try:
            width, height = pdf[page - 1].get_size()
            return float(width), float(height)
        finally:
            pdf.close()

    def render(self, path: Path, page: int, dpi: int, out: Path, crop: BBox | None = None) -> float:
        if _is_image(path):
            from PIL import Image

            image = Image.open(path).convert("RGB")
            scale = 1.0
        else:
            import pypdfium2 as pdfium

            pdf = pdfium.PdfDocument(str(path))
            try:
                scale = dpi / 72.0
                image = pdf[page - 1].render(scale=scale).to_pil().convert("RGB")
            finally:
                pdf.close()
        if crop is not None:
            box = tuple(int(round(v * scale)) for v in crop)
            image = image.crop((max(0, box[0]), max(0, box[1]), min(image.width, box[2]), min(image.height, box[3])))
        image.save(out, format="PNG")
        return scale
