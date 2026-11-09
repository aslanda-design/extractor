"""S07 step 4 (shared calls) - Cut one answer that covers several tasks into one answer per task.

When tasks share a model call, each of them must still read ONLY its own evidence: a value the model took from a
passage that was shown for a different task is the typical mistake of a shared call, so it is dropped here (and
the task is asked again alone when nothing is left).
"""

from ..contracts.evidence import EvidencePassage


def field_answer(data: dict, task_id: str) -> dict | None:
    """The part of a group answer that belongs to `task_id`, or None when the model left it out or malformed it."""
    fields = data.get("fields")
    answer = fields.get(task_id) if isinstance(fields, dict) else None
    return answer if isinstance(answer, dict) and isinstance(answer.get("candidates"), list) else None


def keep_own_evidence(answer: dict, own: dict[str, EvidencePassage], shown: dict[str, EvidencePassage]) -> tuple[dict, list[str]]:
    """`answer` without the candidates that cite a passage shown for another task; plus a note for each one dropped.

    `own`: tag -> the passages retrieved for THIS task; `shown`: tag -> every passage the shared call showed."""
    kept, notes = [], []
    for item in answer.get("candidates") or []:
        tag = str(item.get("passage") or "") if isinstance(item, dict) else ""
        if tag in shown and tag not in own:
            notes.append(f"ignored a candidate ({str(item.get('value'))!r}) citing {tag}: that passage was not retrieved for this task, "
                         "it was shown for another task of the same call")
        else:
            kept.append(item)
    return {**answer, "candidates": kept}, notes
