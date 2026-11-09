"""S10 layer 4 - Consistency: is the answer compatible with the rest of the run?

Dependencies must have been accepted, redacted content is not an answer, and candidates that
were thrown away on the way are reported (a person may want to see them).
"""

from ..contracts.candidate import ProposalOutcome, ValueProposal
from ..contracts.task import TaskSpecification
from ..contracts.validation import Category, ValidationFinding, ValidationLayer
from .findings import finding


def check_consistency(
    task: TaskSpecification, proposal: ValueProposal, accepted_tasks: set[str] | None,
) -> list[ValidationFinding]:
    out = []
    C = ValidationLayer.CONSISTENCY
    if accepted_tasks is not None:
        for dep in task.depends_on:
            if dep not in accepted_tasks:
                out.append(finding(C, Category.DEPENDENCY_NOT_ACCEPTED, f"task '{dep}', which this task depends on, was not accepted", task.id))
    if proposal.outcome == ProposalOutcome.FOUND and any("redacted" in c.flags for c in proposal.chosen):
        out.append(finding(C, Category.REDACTED, "the value is redacted in the document", task.id))
    rejected = [c for c in proposal.candidates if any(f.startswith("rejected:") for f in c.flags)]
    if proposal.outcome == ProposalOutcome.FOUND and rejected:
        out.append(finding(C, Category.CANDIDATE_REJECTED,
                           f"{len(rejected)} other candidate(s) were rejected on the way ({', '.join(repr(c.source_value) for c in rejected[:3])})", task.id))
    return out
