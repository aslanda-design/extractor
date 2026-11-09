"""S05 step 1 - How much evidence do the tasks share?

Two tasks that were handed the same passages would make the model read the same text twice.
This module only MEASURES that (no decision): for every pair of tasks, how many passages
(and tokens) both packages contain.
"""

from itertools import combinations

from ..contracts.evidence import EvidencePackage
from ..contracts.plan import EvidenceOverlap


def measure_overlap(packages: dict[str, EvidencePackage]) -> list[EvidenceOverlap]:
    """One row per pair of tasks that share at least one passage, biggest overlap first."""
    rows = []
    for a, b in combinations(packages, 2):
        passages_a = {p.passage_id: p.token_count for p in packages[a].passages}
        passages_b = {p.passage_id: p.token_count for p in packages[b].passages}
        shared = passages_a.keys() & passages_b.keys()
        if shared:
            rows.append(EvidenceOverlap(
                task_a=a, task_b=b, shared_passages=len(shared), shared_tokens=sum(passages_a[i] for i in shared),
                tokens_a=sum(passages_a.values()), tokens_b=sum(passages_b.values())))
    return sorted(rows, key=lambda r: (-r.containment, -r.shared_tokens))
