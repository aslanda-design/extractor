"""S13 review - Make a review, and read a run through its reviews.

  make_review    checks the target exists, snapshots what the engine said, checks the decision makes sense for it
  reviewed_view  the answers a person ends up with: the engine's, or the reviewer's where there is a standing review. The engine's own
                 answer is always kept next to it (`engine_status`, `engine_value`): the run itself is never changed.

What a decision means:
  accept   the engine's answer is right. Only possible for an answer the engine stands behind (supported or not_found): an
           `uncertain` or `failed` prediction has nothing to accept, so the reviewer corrects it or rejects it.
  correct  the right value. The item becomes supported with that value.
  reject   the document gives no answer for this: the item becomes not_found.
A review whose prediction changed since it was made is `stale` and is NOT applied: a decision about one answer must not decide another.
"""

from typing import Any

from ..contracts.common import TaskStatus
from ..contracts.result import RunReport
from ..contracts.review import Review, ReviewDecision, ReviewedItem, ReviewTarget
from .predictions import locate, prediction_of

STANDS = (TaskStatus.SUPPORTED, TaskStatus.NOT_FOUND)


def make_review(
    report: RunReport, target: ReviewTarget, decision: ReviewDecision, review_id: str, *, value: Any = None, note: str = "", reviewer: str = "",
) -> Review:
    prediction = prediction_of(locate(report, target))
    if decision == ReviewDecision.ACCEPT and prediction.status not in STANDS:
        raise ValueError(f"the engine's answer for {target.label()} is {prediction.status.value}: there is nothing to accept. "
                         "Correct it with the right value, or reject it if the document gives none.")
    return Review(review_id=review_id, run_id=report.run_id, target=target, decision=decision, value=value, note=note, reviewer=reviewer,
                  prediction=prediction)


def reviewed_view(report: RunReport, latest: dict[str, Review]) -> list[ReviewedItem]:
    """One item per task, and one per record or field that has a standing review."""
    items: list[ReviewedItem] = []
    seen: set[str] = set()
    for task in report.results:
        target = ReviewTarget(task_id=task.task_id)
        items.append(_item(report, target, latest.get(target.label())))
        seen.add(target.label())
    for label, review in latest.items():
        if label not in seen:
            items.append(_item(report, review.target, review))
    return items


def _item(report: RunReport, target: ReviewTarget, review: Review | None) -> ReviewedItem:
    now = prediction_of(locate(report, target))
    engine = dict(engine_status=now.status, engine_value=now.value)
    if review is None:
        return ReviewedItem(target=target, source="engine", status=now.status, value=now.value, **engine)
    stale = review.prediction.digest != now.digest
    if stale:
        return ReviewedItem(target=target, source="engine", status=now.status, value=now.value, decision=review.decision, review_id=review.review_id,
                            stale=True, note="a review exists, but the engine's answer changed after it: it is not applied", **engine)
    if review.decision == ReviewDecision.ACCEPT:
        return ReviewedItem(target=target, source="human", status=now.status, value=now.value, decision=review.decision, review_id=review.review_id,
                            note=review.note, **engine)
    if review.decision == ReviewDecision.CORRECT:
        return ReviewedItem(target=target, source="human", status=TaskStatus.SUPPORTED, value=review.value, decision=review.decision,
                            review_id=review.review_id, note=review.note, **engine)
    return ReviewedItem(target=target, source="human", status=TaskStatus.NOT_FOUND, value=None, decision=review.decision, review_id=review.review_id,
                        note=review.note, **engine)
