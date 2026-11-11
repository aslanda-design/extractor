"""S09 step 7 - Audit: was the whole scope really accounted for?

An empty or short list is only believable after this check. Pure code, no model. Each problem is a finding (a fact); the acceptance
profile decides which ones block. Findings:

  numbering_gap          keys that form a sequence skip a member ((a) (b) (d)), or the sequence does not start at its beginning
  possible_missed_record numbered text inside the scope that no record covers
  output_truncated       a discovery answer reached its limit: more records may exist
  discovery_failed       a discovery window failed: part of the scope was never read
  duplicate_key          the same identity was found in two different places
  parse_issue_in_scope   an unresolved parsing problem on a page of the scope
  scope_fallback         (information) the region was not located; the whole document was searched
  boundary_not_located   (information) a record's first words were not found in its block
"""

import re

from ..contracts.document import IssueKind
from ..contracts.passage import IndexedDocument
from ..contracts.validation import Category, RecoveryHint, ValidationFinding, ValidationLayer
from ..validation.findings import finding
from .discovery import Discovery
from .keys import _roman, _shape, _to_roman, sequence_part
from .outline import Tagged, looks_like_item
from .scope import Scope
from .spans import Span

L = ValidationLayer.RECORD_COMPLETENESS


def audit(
    task_id: str, document: IndexedDocument, tagged: list[Tagged], scope: Scope, discovery: Discovery, spans: list[Span],
    duplicates: list[str], check_numbering: bool = True,
) -> list[ValidationFinding]:
    out: list[ValidationFinding] = []
    if scope.fell_back:
        out.append(finding(L, Category.SCOPE_FALLBACK, f"the region of the records was not located; {scope.how}", task_id))
    for why in discovery.failed_windows:
        out.append(finding(L, Category.DISCOVERY_FAILED, why, task_id, RecoveryHint(action="retry_call", reason=why)))
    if discovery.truncated:
        after = f"B{max(s.start for s in spans)}-B{scope.last}" if spans else f"B{scope.first}-B{scope.last}"
        out.append(finding(L, Category.OUTPUT_TRUNCATED, "a discovery answer reached its limit: there may be more records than were listed", task_id,
                           RecoveryHint(action="inspect_passages", reason=after)))
    for key in duplicates:
        out.append(finding(L, Category.DUPLICATE_KEY, f"the identity '{key}' was found in two different places", task_id))
    for s in spans:
        if "boundary_not_located" in s.flags:
            out.append(finding(L, Category.BOUNDARY_NOT_LOCATED, f"the first words of record '{s.key}' were not found in its block", task_id))
    if check_numbering:
        out += numbering_gaps(task_id, spans)
    out += uncovered_items(task_id, tagged, scope, spans)
    out += parse_issues(task_id, document, tagged, scope)
    return out


# --- numbering ------------------------------------------------------------------------------------------------------------


def numbering_gaps(task_id: str, spans: list[Span]) -> list[ValidationFinding]:
    groups: dict[str, list[tuple[str, int | None, Span]]] = {}
    for s in spans:
        part = sequence_part(s.key)
        if part:
            groups.setdefault(part[0], []).append((part[1], part[2], s))
    out = []
    for prefix, members in groups.items():
        if len(members) < 3:
            continue  # too few to call it a sequence
        tokens = [m[0] for m in members]
        if all(m[1] is not None for m in members):
            kind, values = "number", [m[1] for m in members]
        elif any(len(t) > 1 and _roman(t) for t in tokens) and all(_roman(t) for t in tokens):
            kind, values = "roman", [_roman(t) for t in tokens]
        elif all(len(t) == 1 and t.isalpha() for t in tokens):
            kind, values = "letter", [ord(t) - 96 for t in tokens]
        else:
            continue  # names, not a sequence
        wanted = set(range(min(values), max(values) + 1))
        missing = sorted(wanted - set(values))
        name = lambda v: str(v) if kind == "number" else chr(v + 96) if kind == "letter" else _to_roman(v)  # noqa: E731
        where = f" under '{prefix}'" if prefix else ""
        at = dict(zip(values, (m[2] for m in members)))  # sequence value -> the record that has it
        if missing:
            region = _between(at[max(v for v in values if v < missing[0])], at[min(v for v in values if v > missing[-1])])
            out.append(finding(L, Category.NUMBERING_GAP, f"the {kind} sequence{where} skips {', '.join(name(v) for v in missing)}: a record may be missing", task_id,
                               RecoveryHint(action="inspect_passages", reason=region) if region else None))
        if min(values) > 1:
            first = getattr(at[min(values)], "start", None)
            out.append(finding(L, Category.NUMBERING_GAP, f"the {kind} sequence{where} starts at {name(min(values))}, not at the beginning", task_id,
                               RecoveryHint(action="inspect_passages", reason=f"B{max(0, first - 12)}-B{first}") if first is not None else None))
    return out


def _between(before, after) -> str:
    """The block region between two records ("B14-B19"): where a member missing from their sequence must be."""
    a, b = getattr(before, "start", None), getattr(after, "start", None)
    return "" if a is None or b is None else f"B{min(a, b)}-B{max(a, b)}"


# --- scope accounting -------------------------------------------------------------------------------------------------------


def uncovered_items(task_id: str, tagged: list[Tagged], scope: Scope, spans: list[Span]) -> list[ValidationFinding]:
    """Text that starts like the records' own numbering (same family: (a) (b), 4.1, Stage 3...), lies between the first and the last record
    (and just after) and is covered by no record. Numbering of another kind (headings, other lists) is not a missed record."""
    shapes = {s for s in (_shape(sp.key) for sp in spans) if s}
    if not spans or not shapes:
        return []
    covered = {i for sp in spans for i, _, _ in sp.pieces}
    low, high = min(sp.start for sp in spans), min(scope.last, max(sp.end for sp in spans) + 3)
    missed = []
    for t in tagged[low:high + 1]:
        if t.index in covered or t.block.kind.value not in ("paragraph", "list_item") or not looks_like_item(t.block):
            continue
        marker = re.match(r"\s*(\S+(?:\s+\d+)?)", t.block.text)
        if marker and _shape(marker.group(1)) in shapes:
            missed.append(t)
    return [finding(L, Category.POSSIBLE_MISSED_RECORD,
                    f"numbered text on page {t.block.page} is inside the scope and no record covers it: “{' '.join(t.block.text.split())[:70]}”", task_id,
                    RecoveryHint(action="inspect_passages", reason=t.tag)) for t in missed[:8]]


def parse_issues(task_id: str, document: IndexedDocument, tagged: list[Tagged], scope: Scope) -> list[ValidationFinding]:
    pages = {t.block.page for t in tagged[scope.first:scope.last + 1] if t.block.page}
    out = []
    for issue in document.representation.issues:
        if not issue.resolved and issue.page in pages and issue.kind != IssueKind.EMPTY_PAGE:
            out.append(finding(L, Category.PARSE_ISSUE_IN_SCOPE, f"page {issue.page} of the scope has a parsing problem ({issue.kind.value}: {issue.message})",
                               task_id, RecoveryHint(action="reparse_region", document_id=document.document_id, page=issue.page, reason=issue.message)))
    return out
