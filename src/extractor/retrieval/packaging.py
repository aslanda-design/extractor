"""S06 step 7 - Package the evidence for the extraction step.

Entries arrive in PRIORITY order (primary evidence first, neighbours last).
They are admitted while they fit `budget_tokens`; the rest are NOT silently
dropped: each becomes a gap that stays unprocessed until something reads it.
The admitted passages are then put back into reading order and cited block by block.

Roles are provisional (see contracts/evidence.py): retrieved or promoted
passages count as supporting candidates, qualifier passages as qualifying,
and neighbour / reference / definition passages as context.
"""

from dataclasses import dataclass, field
from typing import Callable

from ..contracts.common import EvidenceRole, SourceRef
from ..contracts.coverage import CoverageEntry, CoverageLedger, CoverageState
from ..contracts.evidence import (
    EvidenceItem, EvidenceOrigin, EvidencePackage, EvidencePassage, ExpansionRecord, RetrievalTrace,
)
from .types import Corpus


@dataclass
class Entry:
    passage_id: str
    origin: EvidenceOrigin
    provenance: list[str] = field(default_factory=list)
    score: float | None = None
    promoted: bool = False  # an expansion passage that proved relevant: counts as primary


def role_of(entry: Entry) -> EvidenceRole:
    if entry.origin == EvidenceOrigin.QUALIFIER:
        return EvidenceRole.QUALIFIES
    if entry.origin == EvidenceOrigin.RETRIEVED or entry.promoted:
        return EvidenceRole.SUPPORTS
    return EvidenceRole.CONTEXTUALIZES


def build_package(
    task_id: str,
    corpus: Corpus,
    entries: list[Entry],
    gaps: list[str],
    trace: RetrievalTrace,
    budget_tokens: int,
    count_tokens: Callable[[str], int],
    ledger: CoverageLedger | None = None,
) -> EvidencePackage:
    admitted: list[Entry] = []
    tokens: dict[str, int] = {}
    used = 0
    gaps = list(gaps)
    omitted: set[str] = set()
    for entry in entries:
        passage = corpus.passages[entry.passage_id]
        cost = count_tokens(passage.indexed_text())
        # the first passage is always admitted: nothing can be cut out of it silently
        if admitted and used + cost > budget_tokens:
            gaps.append(f"omitted {entry.passage_id} ({entry.origin.value}, ~{cost} tokens): "
                        f"over the {budget_tokens}-token evidence budget")
            omitted.add(entry.passage_id)
            continue
        if not admitted and cost > budget_tokens:
            gaps.append(f"{entry.passage_id} alone (~{cost} tokens) exceeds the {budget_tokens}-token evidence budget")
        admitted.append(entry)
        tokens[entry.passage_id] = cost
        used += cost

    admitted.sort(key=lambda e: corpus.position(e.passage_id))  # meaningful source order
    passages, items = [], []
    for entry in admitted:
        p = corpus.passages[entry.passage_id]
        block_ids = list(dict.fromkeys(s.block_id for s in p.segments))
        role = role_of(entry)
        passages.append(EvidencePassage(
            passage_id=p.passage_id, document_id=p.document_id, text=p.text, heading_path=p.heading_path,
            pages=p.pages, block_ids=block_ids, token_count=tokens[p.passage_id], origin=entry.origin,
            role=role, provenance=entry.provenance, rerank_score=entry.score))
        for block_id in block_ids:
            items.append(EvidenceItem(ref=SourceRef(document_id=p.document_id, block_id=block_id),
                                      role=role, retrieval_provenance=entry.provenance))
            if ledger is not None:  # surfaced for this task; NOT yet inspected by an extraction step
                ledger.record(CoverageEntry(document_id=p.document_id, block_id=block_id, page=None,
                                            state=CoverageState.RETRIEVED, task_id=task_id))

    trace = trace.model_copy(update={
        "budget_tokens": budget_tokens, "used_tokens": used,
        "expansions": [r.model_copy(update={"included": r.passage_id not in omitted}) for r in trace.expansions]})
    return EvidencePackage(task_id=task_id, items=items, passages=passages, gaps=gaps, trace=trace)
