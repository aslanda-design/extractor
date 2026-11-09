"""ExtractionPlan: which value tasks share one model call (stage S05).

The plan is made from the evidence packages S06 produced, before any model is asked. It only
decides GROUPING: every task is still read, normalized, verified and validated on its own.
"""

from pydantic import computed_field

from .common import StrictModel


class EvidenceOverlap(StrictModel):
    """How much of two tasks' evidence is the same passages."""

    task_a: str
    task_b: str
    shared_passages: int
    shared_tokens: int
    tokens_a: int
    tokens_b: int

    @computed_field  # type: ignore[prop-decorator]
    @property
    def containment(self) -> float:
        """Share of the SMALLER evidence set that the other one also contains (1.0 = one is inside the other)."""
        smaller = min(self.tokens_a, self.tokens_b)
        return self.shared_tokens / smaller if smaller else 0.0


class Batch(StrictModel):
    """Tasks answered by ONE extraction call (a batch of one task is the ordinary case)."""

    task_ids: list[str]
    reason: str  # plain language: why these tasks are together, or why this one is alone
    passage_ids: list[str] = []  # the union of their evidence, in source order (what the call shows)
    tokens: int = 0  # evidence tokens of that union (alone, without the instructions)


class ExtractionPlan(StrictModel):
    batches: list[Batch]
    overlaps: list[EvidenceOverlap] = []  # every pair of tasks that shares at least one passage
    notes: list[str] = []

    def task_order(self) -> list[str]:
        """Task ids in the order the batches list them."""
        return [t for b in self.batches for t in b.task_ids]

    def batch_of(self, task_id: str) -> Batch:
        return next(b for b in self.batches if task_id in b.task_ids)
