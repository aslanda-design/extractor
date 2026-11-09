"""S07 step 2 - Split the evidence so every model call fits the model's context window.

Usually everything fits in one call. When it does not, the passages (kept in
source order) are cut into groups that each fit; every group is extracted
separately and the candidates are reconciled together afterwards. Nothing is
dropped: a passage too big for any call alone still gets its own call and a gap
note, because cutting it silently would hide evidence.
"""

from typing import Callable

from ..contracts.evidence import EvidencePassage


def split_evidence(
    passages: list[EvidencePassage], budget_tokens: int, cost_of: Callable[[EvidencePassage], int]
) -> tuple[list[list[EvidencePassage]], list[str]]:
    """Groups of passages that each cost at most `budget_tokens`, and notes about passages that cannot fit."""
    groups: list[list[EvidencePassage]] = []
    notes: list[str] = []
    used = 0
    for passage in passages:
        cost = cost_of(passage)
        if cost > budget_tokens:
            notes.append(f"{passage.passage_id} alone (~{cost} tokens) exceeds the {budget_tokens}-token call budget")
        if not groups or used + cost > budget_tokens:
            groups.append([])
            used = 0
        groups[-1].append(passage)
        used += cost
    return groups, notes
