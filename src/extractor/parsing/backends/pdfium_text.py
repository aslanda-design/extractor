"""ReferenceTextBackend using pypdfium2: the PDF's own characters with their boxes.

pdfium reports one box per character, in PDF points with a BOTTOM-left origin
(left, bottom, right, top). We group characters into words (split on whitespace) and
convert to the top-left origin used everywhere else. About 10 ms per page.
"""

from pathlib import Path

from ..raw import RefWord

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}


class PdfiumReferenceText:
    name = "pdfium-text"

    def words(self, path: Path, page: int) -> list[RefWord]:
        if path.suffix.lower() in IMAGE_SUFFIXES:
            return []  # an image has no text layer
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(str(path))
        try:
            pdf_page = pdf[page - 1]
            height = float(pdf_page.get_size()[1])
            textpage = pdf_page.get_textpage()
            text = textpage.get_text_range()
            if len(text) != textpage.count_chars():  # indices would not line up with boxes
                return []
            words: list[RefWord | None] = []
            start: int | None = None
            for i, ch in enumerate(text + " "):  # trailing space flushes the last word
                if ch.isspace() or ch == "\x00":
                    if start is not None:
                        words.append(self._word(textpage, text[start:i], start, i, height))
                        start = None
                elif start is None:
                    start = i
            return [w for w in words if w is not None]
        finally:
            pdf.close()

    @staticmethod
    def _word(textpage, text: str, start: int, end: int, height: float) -> RefWord | None:
        boxes = [textpage.get_charbox(i) for i in range(start, end)]
        boxes = [b for b in boxes if b[2] > b[0] and b[3] > b[1]]  # skip zero-size generated characters
        if not boxes:
            return None
        left, right = min(b[0] for b in boxes), max(b[2] for b in boxes)
        bottom, top = min(b[1] for b in boxes), max(b[3] for b in boxes)
        return RefWord(text=text, bbox=(left, height - top, right, height - bottom))
