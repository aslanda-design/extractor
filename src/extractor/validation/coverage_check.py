"""S10 layer 5 - Coverage: was enough of the scope inspected to trust THIS answer?

For an "absent" answer this is the layer that matters. It keeps a RISK REGISTER of everything
that could hide the answer: unresolved parsing issues (page, kind), evidence left out for the
budget, failed model calls and references that were not found. Reading the whole document or
retrying a fixed number of times would cost tokens in proportion to the document. Instead each
risk is scored for relevance to THE TASK with the reranker applied only to the damaged
passages: a damaged table matters to a fee question and not to a name question.

  relevant risk    -> material finding + a recovery hint (what S12 could do)
  irrelevant risk  -> information only
  blank page       -> information (the whole OCR retry ladder found no text on it)

For a "found" answer, an issue on the page it was read from is reported as information.
"""

import re
from typing import Callable

from ..contracts.candidate import ProposalOutcome, ValueProposal
from ..contracts.document import IssueKind
from ..contracts.evidence import EvidencePackage
from ..contracts.passage import IndexedDocument
from ..contracts.task import TaskSpecification
from ..contracts.validation import Category, RecoveryHint, ValidationFinding, ValidationLayer
from .findings import finding

Scorer = Callable[[str, list[str]], list[float]]  # (task query, passage texts) -> relevance 0..1
UNREADABLE = {IssueKind.MISSING_PAGE, IssueKind.NOT_PARSED, IssueKind.OCR_FAILED}
ATTACHMENTS = ("schedule", "exhibit", "appendix", "appendices", "annex")


def check_coverage(
    task: TaskSpecification,
    proposal: ValueProposal,
    package: EvidencePackage,
    documents: list[IndexedDocument],
    *,
    scorer: Scorer | None,
    query: str,
    relevance_ratio: float,
) -> list[ValidationFinding]:
    if proposal.outcome == ProposalOutcome.FOUND:
        return _issues_on_cited_pages(task, proposal, documents)
    if proposal.outcome != ProposalOutcome.ABSENT:
        return []
    reference = max([p.rerank_score for p in package.passages if p.rerank_score is not None], default=1.0) or 1.0
    return [*_parsing_risks(task, documents, scorer, query, reference, relevance_ratio), *_gap_risks(task, proposal.gaps)]


# --- parsing issues ------------------------------------------------------------------------------------------------


def _parsing_risks(task, documents, scorer, query, reference, ratio) -> list[ValidationFinding]:
    out = []
    C = ValidationLayer.COVERAGE
    for doc in documents:
        by_page: dict[int, list] = {}
        for p in doc.passages:
            for page in p.pages:
                by_page.setdefault(page, []).append(p)
        scores: dict[int, float] = {}
        for issue in doc.representation.issues:
            if issue.resolved:
                continue
            where = f"page {issue.page}" if issue.page else "the document"
            hint = RecoveryHint(action="reparse_region", document_id=doc.document_id, page=issue.page, reason=issue.message)
            if issue.kind == IssueKind.EMPTY_PAGE:
                out.append(finding(C, Category.BLANK_PAGE, f"{where} stayed empty after {len(issue.attempts)} OCR attempt(s): probably blank", task.id))
                continue
            texts = [p.indexed_text() for p in by_page.get(issue.page, [])] if issue.page else []
            if issue.kind in UNREADABLE or not texts or scorer is None:
                out.append(finding(C, Category.PARSE_RISK_RELEVANT,
                                   f"{where} could not be read well ({issue.kind.value}: {issue.message}); relevance to this task cannot be judged", task.id, hint))
                continue
            if issue.page not in scores:
                scores[issue.page] = max(scorer(query, texts))
            score = scores[issue.page]
            if score >= ratio * reference:
                out.append(finding(C, Category.PARSE_RISK_RELEVANT,
                                   f"{where} has a parsing problem ({issue.kind.value}: {issue.message}) and its text is relevant to this task (relevance {score:.2f})", task.id, hint))
            else:
                out.append(finding(C, Category.PARSE_RISK_IRRELEVANT,
                                   f"{where} has a parsing problem ({issue.kind.value}) but its text is unrelated to this task (relevance {score:.2f})", task.id))
    return out


def _issues_on_cited_pages(task, proposal, documents) -> list[ValidationFinding]:
    cited = {r.block_id for c in proposal.chosen for r in c.evidence}
    pages = {(d.document_id, b.page) for d in documents for b in d.representation.blocks if b.block_id in cited}
    out = []
    for doc in documents:
        for issue in doc.representation.issues:
            if not issue.resolved and issue.kind != IssueKind.EMPTY_PAGE and (doc.document_id, issue.page) in pages:
                out.append(finding(ValidationLayer.COVERAGE, Category.PARSE_ISSUE_ON_CITED_PAGE,
                                   f"the answer was read from page {issue.page}, which has a parsing problem ({issue.kind.value}: {issue.message})", task.id))
    return out


# --- gaps recorded by retrieval and extraction -----------------------------------------------------------------------


def _gap_risks(task, gaps: list[str]) -> list[ValidationFinding]:
    out = []
    C = ValidationLayer.COVERAGE
    for gap in dict.fromkeys(gaps):
        low = gap.lower()
        if low.startswith("unresolved reference"):
            attachment = any(f"'{word}" in low for word in ATTACHMENTS)
            category = Category.UNRESOLVED_ATTACHMENT if attachment else Category.UNRESOLVED_REFERENCE
            out.append(finding(C, category, gap, task.id, None if attachment else RecoveryHint(action="resolve_reference", reason=gap)))
        elif re.match(r"call \d+/\d+ failed", low):
            out.append(finding(C, Category.MODEL_CALL_FAILED, gap, task.id, RecoveryHint(action="retry_call", reason=gap)))
        else:  # "omitted <passage> ...": evidence that never reached the model
            ids = re.findall(r"\b[\w-]+:c\d+\b", gap)
            out.append(finding(C, Category.CONTENT_OMITTED, gap, task.id, RecoveryHint(action="inspect_passages", passage_ids=ids, reason=gap)))
    return out
