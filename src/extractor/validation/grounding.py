"""S10 layer 2 - Source grounding: does the answer really point at the document?

A genuine quote can still support the wrong answer (that is the semantic layer); here we only
check that the pointers are real: cited blocks exist, the quote was found in the evidence, the
value is in the quote. Pure code.
"""

from ..contracts.candidate import Candidate, ProposalOutcome, ValueProposal
from ..contracts.passage import IndexedDocument
from ..contracts.task import TaskSpecification
from ..contracts.validation import Category, ValidationFinding, ValidationLayer
from .findings import finding


def check_grounding(task: TaskSpecification, proposal: ValueProposal, documents: list[IndexedDocument]) -> list[ValidationFinding]:
    if proposal.outcome != ProposalOutcome.FOUND:
        return []
    blocks = {b.block_id for d in documents for b in d.representation.blocks}
    out = []
    for c in proposal.chosen:
        out += _check_candidate(task, c, blocks)
    return out


def _check_candidate(task: TaskSpecification, c: Candidate, blocks: set[str]) -> list[ValidationFinding]:
    out = []
    G = ValidationLayer.SOURCE_GROUNDING
    label = f"'{c.source_value}'" if c.source_value is not None else f"the derived value ({c.derivation})"
    if not c.evidence:
        out.append(finding(G, Category.NO_EVIDENCE, f"{label} cites no source block", task.id))
    missing = [r.block_id for r in c.evidence if r.block_id not in blocks]
    if missing:
        out.append(finding(G, Category.SOURCE_MISSING, f"{label} cites block(s) that do not exist: {', '.join(missing[:3])}", task.id))
    if c.derived:
        return out  # a computed value has no quote of its own; its inputs were checked when they were extracted
    flags = set(c.flags)
    if flags & {"quote_not_located", "no_quote"}:
        out.append(finding(G, Category.QUOTE_NOT_IN_SOURCE, f"the quote given for {label} was not found in the evidence", task.id))
    if "quote_approximate" in flags:
        out.append(finding(G, Category.QUOTE_APPROXIMATE, f"the quote for {label} matches the source only approximately (OCR or rewording)", task.id))
    if "quote_relocated" in flags:
        out.append(finding(G, Category.QUOTE_RELOCATED, f"the model's quote for {label} is not verbatim; the value was found in the cited passage instead", task.id))
    if "quote_in_other_passage" in flags:
        out.append(finding(G, Category.QUOTE_IN_OTHER_PASSAGE, f"the quote for {label} sits in another passage than the one cited", task.id))
    if "value_not_in_quote" in flags:
        out.append(finding(G, Category.VALUE_NOT_IN_QUOTE, f"{label} does not appear in its own quote", task.id))
    return out
