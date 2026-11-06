"""S03 - Detect parsing problems on a page (pure functions, no I/O).

Each detector returns issues for ONE page. The orchestrator decides what to
retry; whatever stays broken is reported unresolved => coverage limitation.
"""

from ..config.runtime import ParsingConfig
from ..contracts.document import BlockKind, IssueKind, ParsingIssue
from .geometry import horizontal_overlap
from .raw import RawBlock, RawPage
from .routing import compute_signals


def _issue(kind: IssueKind, document_id: str, page: int, message: str, bbox=None) -> ParsingIssue:
    return ParsingIssue(kind=kind, document_id=document_id, page=page, message=message, bbox=bbox)


def detect_empty_page(page: RawPage, document_id: str, config: ParsingConfig) -> list[ParsingIssue]:
    if compute_signals(page).chars < config.min_native_chars:
        return [_issue(IssueKind.EMPTY_PAGE, document_id, page.page,
                       "page has (almost) no extractable text")]
    return []


def detect_corrupted_text(page: RawPage, document_id: str, config: ParsingConfig) -> list[ParsingIssue]:
    ratio = compute_signals(page).garbage_ratio
    if ratio > config.max_garbage_ratio:
        return [_issue(IssueKind.CORRUPTED_TEXT, document_id, page.page,
                       f"{ratio:.1%} of characters are replacement/private-use/control")]
    return []


def detect_reading_order(page: RawPage, document_id: str, config: ParsingConfig) -> list[ParsingIssue]:
    """A block that jumps back UP over a block in the same column is out of order.

    Jumping to the top of a different column (multi-column layouts) is normal
    and not counted.
    """
    boxed = [b for b in page.blocks if b.bbox]
    if len(boxed) < 3:
        return []
    jumps = 0
    for prev, cur in zip(boxed, boxed[1:]):
        same_column = horizontal_overlap(prev.bbox, cur.bbox) > 0.5
        moved_up = cur.bbox[1] < prev.bbox[1] - 2.0  # 2pt tolerance
        jumps += same_column and moved_up
    ratio = jumps / (len(boxed) - 1)
    if ratio > config.max_order_jump_ratio:
        return [_issue(IssueKind.BROKEN_READING_ORDER, document_id, page.page,
                       f"{ratio:.0%} of consecutive blocks jump upward within a column")]
    return []


def _table_problem(block: RawBlock) -> str | None:
    if not block.cells:
        return "table has no cells"
    rows: dict[int, set[int]] = {}
    for c in block.cells:
        rows.setdefault(c.row, set()).add(c.col)
    n_cols = max(c.col for c in block.cells) + 1
    ragged = [r for r, cols in rows.items() if len(cols) < n_cols]
    if ragged:
        return f"{len(ragged)} of {len(rows)} rows have missing cells"
    # A missing header row is NOT reported: header detection is unreliable and many
    # real tables have none. Header flags are kept on the cells for later stages.
    return None


def detect_incomplete_tables(page: RawPage, document_id: str, config: ParsingConfig) -> list[ParsingIssue]:
    issues = []
    for block in page.blocks:
        if block.kind == BlockKind.TABLE:
            problem = _table_problem(block)
            if problem:
                issues.append(_issue(IssueKind.INCOMPLETE_TABLE, document_id, page.page, problem, block.bbox))
    return issues


def detect_page_issues(page: RawPage, document_id: str, config: ParsingConfig) -> list[ParsingIssue]:
    issues: list[ParsingIssue] = []
    for detector in (detect_empty_page, detect_corrupted_text, detect_reading_order, detect_incomplete_tables):
        issues += detector(page, document_id, config)
    return issues
