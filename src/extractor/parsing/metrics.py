"""Proxy quality metrics for parsing, computed against the labeled corpus.

No extra labeling needed: the corpus already records, for every expected
answer, the document, page and a verbatim quote. We check whether the parser
kept that quote and put it on the right page. Matching ignores case,
whitespace and punctuation differences that OCR commonly introduces.
"""

import re

from ..contracts.common import StrictModel
from ..contracts.document import DocumentRepresentation

_WORD = re.compile(r"\w+", re.UNICODE)


def normalize_text(text: str) -> str:
    return " ".join(_WORD.findall(text.casefold()))


class QuoteCheck(StrictModel):
    document: str
    page: int | None
    quote: str
    found: bool  # quote text present anywhere in the document
    on_expected_page: bool | None  # None when the label gave no page


class ParsingMetrics(StrictModel):
    documents: int
    pages: int
    pages_ocr: int
    empty_pages: int
    corrupted_pages: int
    unresolved_issues: int
    quotes_total: int
    quotes_found: int  # quote present anywhere in the parsed document
    quotes_with_page: int  # quotes whose label gave a page
    quotes_on_page: int  # ...and that were found on that page

    @property
    def quote_recall(self) -> float | None:
        return self.quotes_found / self.quotes_total if self.quotes_total else None

    @property
    def page_accuracy(self) -> float | None:
        return self.quotes_on_page / self.quotes_with_page if self.quotes_with_page else None


def check_quote(rep: DocumentRepresentation, document: str, page: int | None, quote: str) -> QuoteCheck:
    wanted = normalize_text(quote)
    full = normalize_text(" ".join(b.text for b in rep.blocks))
    # Compare page by page too: a quote can span blocks, so join the page's blocks.
    on_page = None
    if page is not None:
        page_text = normalize_text(" ".join(b.text for b in rep.blocks if b.page == page))
        on_page = wanted in page_text
    return QuoteCheck(document=document, page=page, quote=quote, found=wanted in full, on_expected_page=on_page)


def summarize(reps: list[DocumentRepresentation], checks: list[QuoteCheck]) -> ParsingMetrics:
    issues = [i for r in reps for i in r.issues]
    labeled = [c for c in checks if c.on_expected_page is not None]
    return ParsingMetrics(
        documents=len(reps),
        pages=sum(len(r.pages) for r in reps),
        pages_ocr=sum(p.route == "ocr" for r in reps for p in r.pages),
        empty_pages=sum(i.kind.value == "empty_page" for i in issues),
        corrupted_pages=sum(i.kind.value == "corrupted_text" for i in issues),
        unresolved_issues=sum(not i.resolved for i in issues),
        quotes_total=len(checks),
        quotes_found=sum(c.found for c in checks),
        quotes_with_page=len(labeled),
        quotes_on_page=sum(bool(c.on_expected_page) for c in labeled),
    )
