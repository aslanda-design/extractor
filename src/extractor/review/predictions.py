"""S13 review - What the engine said about a target, found in a run and fingerprinted.

A review points at a task, at one record of a records task (by its key or its record id), or at one field of a record. `locate` finds
the thing in the run; `prediction_of` says what the engine's answer was (status and value) and a digest of it. The digest is how a
review notices later that the prediction it was made about is not the one in front of it any more.
"""

from dataclasses import dataclass

from ..artifacts.fingerprint import fingerprint
from ..contracts.common import TaskKind, TaskStatus
from ..contracts.record import FieldResult, Record
from ..contracts.result import RunReport, TaskResult
from ..contracts.review import Prediction, ReviewTarget


class UnknownTarget(ValueError):
    """The run has no such task, record or field."""


@dataclass
class Located:
    task: TaskResult
    record: Record | None = None
    field: FieldResult | None = None


def locate(report: RunReport, target: ReviewTarget) -> Located:
    task = next((r for r in report.results if r.task_id == target.task_id), None)
    if task is None:
        raise UnknownTarget(f"the run has no task '{target.task_id}' (tasks: {', '.join(r.task_id for r in report.results)})")
    if target.record_key is None:
        return Located(task)
    if task.kind != TaskKind.RECORDS:
        raise UnknownTarget(f"task '{task.task_id}' is not a records task: it has no records to review")
    record = next((r for r in task.records if target.record_key in (r.key, r.record_id)), None)
    if record is None:
        raise UnknownTarget(f"task '{task.task_id}' has no record '{target.record_key}' (keys: {', '.join(r.key for r in task.records[:12])}...)")
    if target.field_id is None:
        return Located(task, record)
    field = next((f for f in record.fields if f.field_id == target.field_id), None)
    if field is None:
        raise UnknownTarget(f"record '{record.key}' has no field '{target.field_id}' (fields: {', '.join(f.field_id for f in record.fields)})")
    return Located(task, record, field)


def record_status(record: Record) -> TaskStatus:
    """A record is as good as its fields: supported only if every field is."""
    statuses = {f.status for f in record.fields}
    return TaskStatus.SUPPORTED if statuses <= {TaskStatus.SUPPORTED, TaskStatus.NOT_FOUND} and record.fields else TaskStatus.UNCERTAIN


def prediction_of(found: Located) -> Prediction:
    if found.field is not None:
        status, value, extra = found.field.status, found.field.value, None
    elif found.record is not None:
        status, value, extra = record_status(found.record), {f.field_id: f.value for f in found.record.fields}, None
    elif found.task.kind == TaskKind.RECORDS:
        status = found.task.status
        value = {"records": len(found.task.records), "completeness": found.task.completeness.value if found.task.completeness else None}
        extra = [(r.key, [(f.field_id, f.status.value, f.value) for f in r.fields]) for r in found.task.records]
    else:
        status, value, extra = found.task.status, found.task.value, found.task.alternatives
    return Prediction(status=status, value=value, digest=fingerprint(status.value, value, extra)[:16])
