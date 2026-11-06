"""S03 - Parse one document: route pages, run OCR where needed, retry, report issues.

    for each page:
        native text usable?  --yes--> keep it (+ OCR any big untexted picture region)
                             --no --> OCR ladder: ocr_dpi -> ocr_hires_dpi
        detect problems      --> retry with OCR when it can help
        still broken         --> issue stays `resolved=False` (a coverage limitation)

Nothing here imports a parsing library: backends are injected (see backends/base.py).
"""

import tempfile
from pathlib import Path

from ..config.runtime import ParsingConfig
from ..contracts.document import (
    DocumentRepresentation,
    IssueKind,
    ParsingIssue,
    SourceBlock,
    SourceTable,
)
from .backends.base import NativeBackend, OcrBackend, PageRenderer
from .detectors import detect_page_issues
from .geometry import scale
from .normalize import page_info, to_source_blocks
from .raw import BBox, NativeExtraction, RawBlock, RawPage
from .routing import Route, RouteDecision, compute_signals, decide_route, regions_needing_ocr

IMAGE_FORMATS = {"png", "jpg", "jpeg", "tif", "tiff"}
RETRYABLE_BY_OCR = {IssueKind.BROKEN_READING_ORDER, IssueKind.INCOMPLETE_TABLE}


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


def _ocr_page(
    page_ocr: PageOcr, path: Path, number: int, size: tuple[float, float], config: ParsingConfig
) -> tuple[RawPage, list[str]]:
    """OCR ladder for a whole page. Returns the best page and the rungs tried."""
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


def _merge_region_blocks(page: RawPage, region_blocks: list[RawBlock]) -> RawPage:
    """Insert OCR'd region blocks into the native reading order by vertical position."""
    merged = list(page.blocks)
    for block in sorted(region_blocks, key=lambda b: b.bbox[1] if b.bbox else 0):
        top = block.bbox[1] if block.bbox else 0
        pos = next((i for i, b in enumerate(merged) if b.bbox and b.bbox[1] > top), len(merged))
        merged.insert(pos, block)
    return page.model_copy(update={"blocks": merged})


def _parse_page(
    document_id: str, path: Path, number: int, native: RawPage | None,
    size: tuple[float, float], page_ocr: PageOcr, config: ParsingConfig,
) -> tuple[RawPage, str, str, list[ParsingIssue]]:
    """Return (final page, route, reason, issues) for one page."""
    issues: list[ParsingIssue] = []

    if native is None:  # the native pass returned nothing for this page
        page, attempts = _ocr_page(page_ocr, path, number, size, config)
        issues.append(ParsingIssue(
            kind=IssueKind.MISSING_PAGE, document_id=document_id, page=number,
            message="page missing from the native parse", attempts=["native"] + attempts,
            resolved=compute_signals(page).chars >= config.min_native_chars))
        return page, Route.OCR, "missing from native parse", issues

    decision: RouteDecision = decide_route(native, config)
    if decision.route == Route.OCR:
        page, attempts = _ocr_page(page_ocr, path, number, size, config)
        found = compute_signals(page).chars >= config.min_native_chars
        if not found:
            issues.append(ParsingIssue(
                kind=IssueKind.OCR_FAILED if "error" in "".join(attempts) else IssueKind.EMPTY_PAGE,
                document_id=document_id, page=number,
                message="no text found after OCR (the page may be blank)",
                attempts=["native"] + attempts, resolved=False))
        elif decision.signals.garbage_ratio > config.max_garbage_ratio:
            issues.append(ParsingIssue(
                kind=IssueKind.CORRUPTED_TEXT, document_id=document_id, page=number,
                message=decision.reason, attempts=["native"] + attempts, resolved=True))
        if found:  # OCR output can have its own problems (tables, order)
            issues += [i.model_copy(update={"attempts": ["native"] + attempts})
                       for i in detect_page_issues(page, document_id, config)]
        return page, Route.OCR, decision.reason, issues

    # Native page: first OCR big pictures that carry no text.
    page = native
    region_blocks: list[RawBlock] = []
    for region in regions_needing_ocr(native, config):
        try:
            region_blocks += page_ocr.run(path, number, config.ocr_dpi, crop=region)
        except Exception as exc:
            issues.append(ParsingIssue(
                kind=IssueKind.OCR_FAILED, document_id=document_id, page=number, bbox=region,
                message=f"region OCR failed: {exc}", attempts=["native", "ocr"], resolved=False))
    if region_blocks:
        page = _merge_region_blocks(page, region_blocks)

    # Then look for problems a full-page OCR might fix.
    found = detect_page_issues(page, document_id, config)
    retryable = [i for i in found if i.kind in RETRYABLE_BY_OCR]
    others = [i for i in found if i.kind not in RETRYABLE_BY_OCR]
    if not retryable:
        return page, Route.NATIVE, decision.reason, issues + others

    ocr_page, attempts = _ocr_page(page_ocr, path, number, size, config)
    remaining = {i.kind for i in detect_page_issues(ocr_page, document_id, config)}
    fixed = [i for i in retryable if i.kind not in remaining]
    if fixed and compute_signals(ocr_page).chars >= config.min_native_chars:
        resolved = [i.model_copy(update={"attempts": ["native"] + attempts, "resolved": True}) for i in fixed]
        still = [i.model_copy(update={"attempts": ["native"] + attempts}) for i in retryable if i.kind in remaining]
        return ocr_page, Route.OCR, f"retry after {', '.join(i.kind.value for i in fixed)}", \
            issues + others + resolved + still
    unresolved = [i.model_copy(update={"attempts": ["native"] + attempts}) for i in retryable]
    return page, Route.NATIVE, decision.reason, issues + others + unresolved


def parse_document(
    document_id: str,
    path: Path,
    fmt: str,
    config: ParsingConfig,
    native: NativeBackend,
    ocr: OcrBackend,
    renderer: PageRenderer,
) -> tuple[DocumentRepresentation, NativeExtraction | None]:
    """Parse one PDF or image. Also returns the raw native output (for the artifact store)."""
    if fmt != "pdf" and fmt not in IMAGE_FORMATS:
        issue = ParsingIssue(kind=IssueKind.NOT_PARSED, document_id=document_id,
                             message=f"format '.{fmt}' is not supported by the parser yet")
        return DocumentRepresentation(document_id=document_id, issues=[issue]), None

    page_ocr = PageOcr(ocr, renderer, config)
    extraction: NativeExtraction | None = None
    native_pages: dict[int, RawPage] = {}
    issues: list[ParsingIssue] = []

    if fmt == "pdf":
        try:
            extraction = native.extract(path)
            native_pages = {p.page: p for p in extraction.pages}
        except Exception as exc:
            issues.append(ParsingIssue(
                kind=IssueKind.NOT_PARSED, document_id=document_id,
                message=f"native parse failed, falling back to OCR for every page: {exc}",
                attempts=["native"]))

    try:
        total = renderer.page_count(path)
    except Exception as exc:  # unreadable / corrupted file: report it, never crash
        issues.append(ParsingIssue(
            kind=IssueKind.NOT_PARSED, document_id=document_id,
            message=f"the file could not be opened as a {fmt}: {exc}", attempts=["open"]))
        return DocumentRepresentation(document_id=document_id, issues=issues), extraction
    blocks: list[SourceBlock] = []
    tables: list[SourceTable] = []
    pages = []
    for number in range(1, total + 1):
        size = renderer.page_size(path, number)
        page, route, reason, page_issues = _parse_page(
            document_id, path, number, native_pages.get(number) if fmt == "pdf" else None, size, page_ocr, config)
        if fmt != "pdf":  # image files have no native layer: a note, not a "missing page"
            page_issues = [i for i in page_issues if i.kind != IssueKind.MISSING_PAGE]
            reason = "image file"
        b, t = to_source_blocks(document_id, page, from_ocr=route == Route.OCR)
        blocks += b
        tables += t
        pages.append(page_info(page, str(route), reason))
        issues += page_issues

    return DocumentRepresentation(
        document_id=document_id, pages=pages, blocks=blocks, tables=tables, issues=issues
    ), extraction
