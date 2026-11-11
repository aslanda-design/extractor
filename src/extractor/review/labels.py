"""S13 review - Reviewed corrections as evaluation labels: the loop that makes review data useful beyond one run.

`corrections_as_labels(report, latest)` turns what people decided into the `expected:` section of a corpus case (the format of
`evaluation/corpus/_template/case.template.yaml`), so the engine's mistakes become tomorrow's test data. It writes labels only where a
person decided; nothing the engine said on its own becomes a label (the corpus rule: labels are human-reviewed). Evidence is the
quote and page the engine cited for the answer, which the reviewer confirmed or corrected: check it before adding the case.

The result is NOT added to the corpus by itself: a case needs its document files and a spec, and a person to place and check it.
Records tasks are exported only when every record field is either accepted by the engine and confirmed, or decided by a person;
anything else is listed under `needs_labeling`.
"""

import re

from ..contracts.common import TaskKind, TaskStatus
from ..contracts.result import RunReport, TaskResult
from ..contracts.review import Review, ReviewDecision, ReviewTarget

BLOCK = re.compile(r"^(?P<doc>.+?):p(?P<page>\d+):b\d+$")


def evidence_of(report: RunReport, task: TaskResult) -> list[dict]:
    out = []
    for link in task.evidence:
        m = BLOCK.match(link.ref.block_id)
        out.append({"document": report.execution.document_names.get(link.ref.document_id, link.ref.document_id),
                    **({"page": int(m.group("page"))} if m else {}), **({"quote": link.quote} if link.quote else {}), "role": link.role.value})
    return out


def corrections_as_labels(report: RunReport, latest: dict[str, Review]) -> dict:
    expected: dict[str, dict] = {}
    needs: list[str] = []
    for task in report.results:
        whole = latest.get(ReviewTarget(task_id=task.task_id).label())
        if task.kind != TaskKind.RECORDS:
            if whole is None:
                continue
            label = _scalar(report, task, whole)
            if label is None:
                needs.append(f"{task.task_id}: the review is stale or does not decide a value")
            else:
                expected[task.task_id] = label
            continue
        label, missing = _records(report, task, latest, whole)
        if label is not None:
            expected[task.task_id] = label
        elif missing:
            needs.append(f"{task.task_id}: {', '.join(missing[:6])}{' ...' if len(missing) > 6 else ''}")
    return {"case_id": report.run_id, "documents": sorted(report.execution.document_names.values()), "expected": expected, "needs_labeling": needs,
            "note": "Reviewed corrections. Place the documents under evaluation/corpus/documents/, write or reuse a spec, check the evidence, then save this as a case."}


def _scalar(report: RunReport, task: TaskResult, review: Review) -> dict | None:
    if review.decision == ReviewDecision.CORRECT:
        return {"status": "supported", "value": review.value, "evidence": evidence_of(report, task), "notes": review.note or "corrected by a reviewer"}
    if review.decision == ReviewDecision.REJECT:
        return {"status": "not_found", "notes": review.note or "a reviewer found no answer in the document"}
    if task.status == TaskStatus.SUPPORTED:
        return {"status": "supported", "value": task.value, "evidence": evidence_of(report, task), "notes": review.note or "confirmed by a reviewer"}
    return {"status": "not_found", "notes": review.note or "confirmed by a reviewer"}


def _records(report: RunReport, task: TaskResult, latest: dict[str, Review], whole: Review | None) -> tuple[dict | None, list[str]]:
    if whole is None and not any(label.startswith(f"{task.task_id} / ") for label in latest):
        return None, []
    records, missing = [], []
    for record in task.records:
        record_review = latest.get(ReviewTarget(task_id=task.task_id, record_key=record.key).label())
        if record_review is not None and record_review.decision == ReviewDecision.REJECT:
            continue  # a reviewer says this is not a record
        fields = {}
        for f in record.fields:
            fr = latest.get(ReviewTarget(task_id=task.task_id, record_key=record.key, field_id=f.field_id).label())
            if fr is not None and fr.decision == ReviewDecision.CORRECT:
                fields[f.field_id] = fr.value
            elif fr is not None and fr.decision == ReviewDecision.REJECT:
                continue
            elif f.status == TaskStatus.SUPPORTED and (fr is not None or whole is not None):
                fields[f.field_id] = f.value
            elif f.status != TaskStatus.NOT_FOUND:
                missing.append(f"{record.key}.{f.field_id}")
        records.append({"fields": fields})
    if missing:
        return None, missing
    return {"status": "supported", "completeness": "checked", "records": records, "notes": "reviewed collection"}, []
