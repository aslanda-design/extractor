"""S13 review - Where reviews live: next to the runs in the artifact store, never inside them.

One file per run (`reviews/<run_id>.json`) holding the list of its reviews, oldest first. The store only ever appends: a later review of
the same target supersedes an earlier one in `latest`, and the earlier one stays in the file, so a decision can be audited.
"""

from pydantic import BaseModel

from ..artifacts.store import ArtifactStore
from ..contracts.review import Review

KIND = "reviews"


class _File(BaseModel):
    reviews: list[Review] = []


class ReviewStore:
    def __init__(self, store: ArtifactStore):
        self.store = store

    def for_run(self, run_id: str) -> list[Review]:
        saved = self.store.get_model(KIND, run_id, _File)
        return saved.reviews if saved else []

    def next_id(self, run_id: str) -> str:
        return f"rv-{len(self.for_run(run_id)) + 1:04d}"

    def add(self, review: Review) -> Review:
        existing = self.for_run(review.run_id)
        if any(r.review_id == review.review_id for r in existing):
            raise ValueError(f"review {review.review_id} already exists: reviews are never overwritten")
        self.store.put(KIND, review.run_id, _File(reviews=[*existing, review]))
        return review

    def latest(self, run_id: str) -> dict[str, Review]:
        """The review that stands for each target (the last one made), by the target's label."""
        out: dict[str, Review] = {}
        for r in self.for_run(run_id):
            out[r.target.label()] = r
        return out
