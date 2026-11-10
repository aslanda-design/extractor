"""S10 (record completeness) + S11 for a collection: the task status and `completeness` of a records task.

Findings from the audit are facts; the acceptance profile decides which block (same `policy.classify` as every other task). Then:

  failed      every discovery window failed: nothing was read (never `not_found`)
  not_found   no record found AND the audit found nothing in the way (the empty list is a real answer only after the inspection)
  supported   completeness is `checked` AND every record has all its required fields accepted
  uncertain   anything else: the records read so far are KEPT in the result, each field with its own status, so accepted work is never lost

completeness: `checked` = no blocking audit finding; `partial` = records exist but a finding blocks; `unknown` = discovery failed.
"""

from ..config.runtime import AcceptanceSettings
from ..contracts.common import Completeness, TaskStatus
from ..contracts.task import OnAbsent, TaskSpecification
from ..contracts.validation import AcceptanceDecision, Category, ValidationFinding, ValidationLayer, ValidationReport
from ..records.records_pipeline import RecordsOutcome
from .findings import finding
from .policy import classify


def decide_records(task: TaskSpecification, outcome: RecordsOutcome, settings: AcceptanceSettings) -> tuple[ValidationReport, AcceptanceDecision, Completeness]:
    findings: list[ValidationFinding] = list(outcome.findings)
    for r in outcome.records:
        for o in r.outcomes:
            if not o.accepted:
                findings.append(finding(
                    ValidationLayer.RECORD_COMPLETENESS, Category.RECORD_FIELD_NOT_ACCEPTED,
                    f"record '{r.record.key}': field '{o.result.field_id}' is {o.result.status.value}"
                    + (f" ({o.validated.decision.reasons[0]})" if o.validated.decision.reasons else ""), task.id))
    if not outcome.records and not outcome.discovery_failed:
        findings.append(finding(ValidationLayer.RECORD_COMPLETENESS, Category.NO_RECORDS, "no record was found in the scope", task.id))
    report = classify(findings, settings)
    material = [f for f in report.material if f.category != Category.RECORD_FIELD_NOT_ACCEPTED]  # collection-level blockers (+ no_records)
    field_problems = [f for f in report.material if f.category == Category.RECORD_FIELD_NOT_ACCEPTED]
    hints = [f.recovery for f in report.material if f.recovery]
    reasons = [f.message for f in report.material][:6]

    def decision(status: TaskStatus, *why: str) -> AcceptanceDecision:
        return AcceptanceDecision(status=status, policy=settings.profile, reasons=[*why, *reasons][:6], blocking=report.material, hints=hints)

    if outcome.discovery_failed:
        return report, decision(TaskStatus.FAILED, "every discovery call failed: nothing was read"), Completeness.UNKNOWN
    blockers = [f for f in material if f.category != Category.NO_RECORDS]
    completeness = Completeness.PARTIAL if blockers else Completeness.CHECKED
    if not outcome.records:
        if blockers:
            return report, decision(TaskStatus.UNCERTAIN, "no record was found, but the inspection is incomplete:"), Completeness.PARTIAL
        status = TaskStatus.NOT_FOUND if task.rules.on_absent == OnAbsent.NOT_FOUND else TaskStatus.UNCERTAIN
        return report, decision(status, "no record is stated in the scope; the inspection was complete"), Completeness.CHECKED
    if blockers:
        return report, decision(TaskStatus.UNCERTAIN, f"{len(outcome.records)} record(s) were read, but the collection cannot be called complete:"), completeness
    if field_problems:
        bad = len({f.message.split("'")[1] for f in field_problems})
        return report, decision(TaskStatus.UNCERTAIN, f"the collection is complete but {bad} of {len(outcome.records)} record(s) have a field that was not accepted:"), Completeness.CHECKED
    return report, decision(TaskStatus.SUPPORTED, f"accepted: {len(outcome.records)} record(s), every required field accepted, the audit found no gap ({settings.profile} policy)"), Completeness.CHECKED
