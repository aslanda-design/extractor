"""S03 - THE PARSING PIPELINE, in one file. Read this to see how a document becomes blocks.

    parse_document(path)
      D1  open the file                      page count; refuse unsupported formats
      D2  native pass (whole document)       layout parser -> one RawPage per page (structure + text)
      D3  for every page: parse_page(...)    see below
      D4  assemble                           stable block ids, PageInfo, DocumentRepresentation

    parse_page(page)
      P1  page missing from the native pass?        -> OCR it (ladder), report MISSING_PAGE
      P2  route: is the native text usable?         routing.decide_route
      P3  route = OCR                               OCR ladder: ocr_dpi -> ocr_hires_dpi, then stop
      P4  route = native: big pictures with no text -> OCR just those regions
      P5  COMPLETENESS CHECK (reconcile.py)         compare the layout parse with the PDF's own
                                                    text layer; restore any word the layout parse
                                                    dropped (no OCR needed: the text exists)
      P6  look for remaining problems               reading order, broken tables, ...
            fixable by OCR?                         -> OCR the whole page, keep it only if better
            otherwise                               -> keep native, issue stays unresolved

Why P5 exists: the layout parser is the only component that cannot notice what it dropped, and
every other check (P2, P6) looks at the blocks it produced. P5 is the one step that looks at the
PDF itself. Helpers live in their own modules; no parsing library is imported here (backends are
injected, see backends/base.py), so the whole pipeline is testable with fakes.
"""

from dataclasses import dataclass, field
from pathlib import Path

from ..config.runtime import ParsingConfig
from ..contracts.document import (
    DocumentRepresentation,
    IssueKind,
    ParsingIssue,
    SourceBlock,
    SourceTable,
)
from ..events import emit, progress
from .backends.base import NativeBackend, OcrBackend, PageRenderer, ReferenceTextBackend
from .detectors import detect_page_issues
from .normalize import page_info, to_source_blocks
from .page_ocr import PageOcr, ocr_page
from .raw import NativeExtraction, RawBlock, RawPage
from .reconcile import insert_in_reading_order, reconcile_page, reference_is_trusted
from .routing import Route, RouteDecision, compute_signals, decide_route, regions_needing_ocr

IMAGE_FORMATS = {"png", "jpg", "jpeg", "tif", "tiff"}
RETRYABLE_BY_OCR = {IssueKind.BROKEN_READING_ORDER, IssueKind.INCOMPLETE_TABLE}


@dataclass
class ParsedPage:
    """Everything parse_page decided about one page."""

    page: RawPage
    route: Route
    reason: str
    issues: list[ParsingIssue] = field(default_factory=list)
    layout_coverage: float | None = None  # share of text-layer words the layout parse had (None = unchecked)
    recovered_words: int = 0


# ======================================================================================
# One page
# ======================================================================================

def parse_page(
    document_id: str, path: Path, number: int, native: RawPage | None, size: tuple[float, float],
    page_ocr: PageOcr, config: ParsingConfig, reference: ReferenceTextBackend | None = None,
) -> ParsedPage:
    issues: list[ParsingIssue] = []

    # P1 - the native pass returned nothing for this page: OCR is the only source left.
    if native is None:
        page, attempts = ocr_page(page_ocr, path, number, size, config)
        issues.append(ParsingIssue(
            kind=IssueKind.MISSING_PAGE, document_id=document_id, page=number,
            message="page missing from the native parse", attempts=["native"] + attempts,
            resolved=compute_signals(page).chars >= config.min_native_chars))
        return ParsedPage(page, Route.OCR, "missing from native parse", issues)

    # P2 - is the native text layer usable at all?
    decision: RouteDecision = decide_route(native, config)

    # P3 - no: OCR the whole page (200 dpi, then 300 dpi if still empty).
    if decision.route == Route.OCR:
        page, attempts = ocr_page(page_ocr, path, number, size, config)
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
        return ParsedPage(page, Route.OCR, decision.reason, issues)

    # P4 - yes: keep native text, but OCR big pictures that carry no text (pasted scans, stamps).
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
        page = insert_in_reading_order(page, region_blocks)

    # P5 - completeness: did the layout parse keep every word the PDF contains?
    coverage_ratio: float | None = None
    recovered_words = 0
    if config.reconcile and reference is not None:
        page, coverage_ratio, recovered_words, completeness_issues = _check_completeness(
            document_id, path, number, page, reference, config)
        issues += completeness_issues

    # P6 - remaining problems (reading order, tables). Fixable ones get one whole-page OCR retry.
    found = detect_page_issues(page, document_id, config)
    retryable = [i for i in found if i.kind in RETRYABLE_BY_OCR]
    others = [i for i in found if i.kind not in RETRYABLE_BY_OCR]
    if not retryable:
        return ParsedPage(page, Route.NATIVE, decision.reason, issues + others, coverage_ratio, recovered_words)

    retried, attempts = ocr_page(page_ocr, path, number, size, config)
    remaining = {i.kind for i in detect_page_issues(retried, document_id, config)}
    fixed = [i for i in retryable if i.kind not in remaining]
    if fixed and compute_signals(retried).chars >= config.min_native_chars:
        resolved = [i.model_copy(update={"attempts": ["native"] + attempts, "resolved": True}) for i in fixed]
        still = [i.model_copy(update={"attempts": ["native"] + attempts}) for i in retryable if i.kind in remaining]
        return ParsedPage(retried, Route.OCR, f"retry after {', '.join(i.kind.value for i in fixed)}",
                          issues + others + resolved + still, coverage_ratio, 0)
    unresolved = [i.model_copy(update={"attempts": ["native"] + attempts}) for i in retryable]
    return ParsedPage(page, Route.NATIVE, decision.reason, issues + others + unresolved,
                      coverage_ratio, recovered_words)


def _check_completeness(
    document_id: str, path: Path, number: int, page: RawPage, reference: ReferenceTextBackend,
    config: ParsingConfig,
) -> tuple[RawPage, float | None, int, list[ParsingIssue]]:
    """P5. Returns (page with missing words restored, coverage of the layout parse, words restored, issues)."""
    try:
        words = reference.words(path, number)
    except Exception as exc:  # the check itself failed: say so, never pretend the page is complete
        emit("S03", f"page {number}: completeness check unavailable ({exc})", "warn")
        return page, None, 0, [ParsingIssue(
            kind=IssueKind.TEXT_NOT_COVERED, document_id=document_id, page=number,
            message=f"completeness check could not run: {exc}", attempts=["native"], resolved=False)]
    if not reference_is_trusted(words, config):
        return page, None, 0, []  # no usable yardstick (little or corrupted text layer): nothing to compare
    result = reconcile_page(page, words, config)
    cov = result.coverage
    if not result.recovered_blocks:
        emit("S03", f"page {number}: layout parse covers {cov.ratio:.0%} of the text layer "
                    f"({cov.covered_words}/{cov.total_words} words)")
        return page, cov.ratio, 0, []
    missing = len(cov.orphans)
    emit("S03", f"page {number}: layout parse covered only {cov.ratio:.0%} of the text layer "
                f"({cov.covered_words}/{cov.total_words} words); restored {missing} missing word(s) "
                f"as {result.recovered_blocks} block(s) from the PDF text layer", "warn")
    issue = ParsingIssue(
        kind=IssueKind.TEXT_NOT_COVERED, document_id=document_id, page=number,
        message=f"the layout parse missed {missing} of {cov.total_words} words ({1 - cov.ratio:.0%}); "
                f"restored from the PDF text layer as {result.recovered_blocks} block(s)",
        attempts=["native", "reference"], resolved=True)
    return result.page, cov.ratio, missing, [issue]


# ======================================================================================
# One document
# ======================================================================================

def parse_document(
    document_id: str,
    path: Path,
    fmt: str,
    config: ParsingConfig,
    native: NativeBackend,
    ocr: OcrBackend,
    renderer: PageRenderer,
    reference: ReferenceTextBackend | None = None,
) -> tuple[DocumentRepresentation, NativeExtraction | None]:
    """Parse one PDF or image. Also returns the raw native output (for the artifact store)."""
    # D1 - only PDFs and images are parseable.
    if fmt != "pdf" and fmt not in IMAGE_FORMATS:
        issue = ParsingIssue(kind=IssueKind.NOT_PARSED, document_id=document_id,
                             message=f"format '.{fmt}' is not supported by the parser yet")
        return DocumentRepresentation(document_id=document_id, issues=[issue]), None

    page_ocr = PageOcr(ocr, renderer, config)
    extraction: NativeExtraction | None = None
    native_pages: dict[int, RawPage] = {}
    issues: list[ParsingIssue] = []

    # D2 - one native pass over the whole PDF (images have no native layer).
    if fmt == "pdf":
        try:
            emit("S03", f"reading the native text layer of {path.name}")
            extraction = native.extract(path)
            native_pages = {p.page: p for p in extraction.pages}
        except Exception as exc:
            emit("S03", f"native parse failed, every page will use OCR: {exc}", "warn")
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
    emit("S03", f"{path.name}: {total} page(s)")

    # D3 - every page goes through parse_page (P1..P6 above).
    blocks: list[SourceBlock] = []
    tables: list[SourceTable] = []
    pages = []
    for number in range(1, total + 1):
        size = renderer.page_size(path, number)
        result = parse_page(document_id, path, number, native_pages.get(number) if fmt == "pdf" else None,
                            size, page_ocr, config, reference if fmt == "pdf" else None)
        page_issues, reason = result.issues, result.reason
        if fmt != "pdf":  # image files have no native layer: a note, not a "missing page"
            page_issues = [i for i in page_issues if i.kind != IssueKind.MISSING_PAGE]
            reason = "image file"
        b, t = to_source_blocks(document_id, result.page, from_ocr=result.route == Route.OCR)
        blocks += b
        tables += t
        pages.append(page_info(result.page, str(result.route), reason,
                               result.layout_coverage, result.recovered_words))
        issues += page_issues
        progress("S03", number, total, "pages parsed")
        unresolved = [i for i in page_issues if not i.resolved]
        emit("S03", f"page {number}/{total}: {result.route} ({reason}); {len(b)} block(s), {len(t)} table(s)"
                    + (f", {result.recovered_words} word(s) restored" if result.recovered_words else "")
                    + (f", {len(unresolved)} unresolved issue(s)" if unresolved else ""),
             "warn" if unresolved else "info")

    # D4 - assemble the public representation.
    ocr_pages = sum(1 for p in pages if p.route == str(Route.OCR))
    restored = sum(p.recovered_words for p in pages)
    emit("S03", f"parsing finished: {len(blocks)} block(s), {len(tables)} table(s), {ocr_pages} page(s) via OCR, "
                f"{restored} word(s) restored from the text layer, "
                f"{sum(1 for i in issues if not i.resolved)} unresolved issue(s)")
    return DocumentRepresentation(
        document_id=document_id, pages=pages, blocks=blocks, tables=tables, issues=issues
    ), extraction
