"""S11 - The acceptance policy: which findings block, and what each outcome becomes.

Acceptance belongs to the engine. Nothing here reads a model's confidence or a retrieval score
(on the corpus neither separated right answers from wrong ones). Profiles say which finding
categories are material:

  strict    everything that could be a doubt blocks (also: rejected candidates, a quote found in another
            passage, parsing problems on the cited page, blank pages, missing attachments)
  standard  grounding, normalization, consistency, RELEVANT coverage risks and semantic failures block
  lenient   standard, minus approximate quotes and minus unresolved references inside the document

`acceptance.block` / `acceptance.allow` in the runtime config change single categories.
"""

from ..config.runtime import AcceptanceSettings
from ..contracts.candidate import ProposalOutcome, ValueProposal
from ..contracts.common import TaskStatus
from ..contracts.task import OnAbsent, TaskSpecification
from ..contracts.validation import (
    AcceptanceDecision, Category as C, FindingSeverity, RecoveryHint, ValidationFinding, ValidationReport,
)

STANDARD = {
    C.BAD_TYPE, C.NO_VALUE, C.NO_EVIDENCE, C.SOURCE_MISSING, C.QUOTE_NOT_IN_SOURCE, C.QUOTE_APPROXIMATE, C.VALUE_NOT_IN_QUOTE,
    C.NOT_NORMALIZED, C.SCALE_MISMATCH, C.DIGITS_WORDS_DISAGREE, C.REDACTED, C.DEPENDENCY_NOT_ACCEPTED,
    C.PARSE_RISK_RELEVANT, C.CONTENT_OMITTED, C.MODEL_CALL_FAILED, C.UNRESOLVED_REFERENCE, C.SEMANTIC_UNSUPPORTED,
    C.MODEL_NOT_QUALIFIED,
}
PROFILES: dict[str, set[C]] = {
    "standard": STANDARD,
    "strict": STANDARD | {C.QUOTE_IN_OTHER_PASSAGE, C.QUOTE_RELOCATED, C.CANDIDATE_REJECTED, C.PARSE_ISSUE_ON_CITED_PAGE, C.PARSE_RISK_IRRELEVANT,
                          C.BLANK_PAGE, C.UNRESOLVED_ATTACHMENT, C.SEMANTIC_CHECK_FAILED},
    "lenient": STANDARD - {C.QUOTE_APPROXIMATE, C.UNRESOLVED_REFERENCE},
}


def blocking_categories(settings: AcceptanceSettings) -> set[str]:
    blocking = {c.value for c in PROFILES[settings.profile]}
    return (blocking | set(settings.block)) - set(settings.allow)


def classify(findings: list[ValidationFinding], settings: AcceptanceSettings) -> ValidationReport:
    """The same findings, with `material` set for the categories this profile blocks on."""
    blocking = blocking_categories(settings)
    return ValidationReport(findings=[
        f.model_copy(update={"severity": FindingSeverity.MATERIAL if f.category in blocking else FindingSeverity.INFO})
        for f in findings])


def decide(
    task: TaskSpecification, proposal: ValueProposal, report: ValidationReport, settings: AcceptanceSettings,
) -> AcceptanceDecision:
    material = report.material
    hints: list[RecoveryHint] = [f.recovery for f in material if f.recovery]
    reasons = [f.message for f in material]
    outcome = proposal.outcome

    def decision(status: TaskStatus, *why: str) -> AcceptanceDecision:
        return AcceptanceDecision(status=status, policy=settings.profile, reasons=[*why, *reasons][:6],
                                  blocking=material, hints=hints)

    if outcome == ProposalOutcome.FAILED:
        return decision(TaskStatus.FAILED, proposal.reason)
    if outcome == ProposalOutcome.UNCERTAIN:
        return decision(TaskStatus.UNCERTAIN, proposal.reason)
    if outcome == ProposalOutcome.FOUND:
        if material:
            return decision(TaskStatus.UNCERTAIN, "a value was found but it cannot be accepted:")
        sources = len({r.block_id for c in proposal.chosen for r in c.evidence})
        return decision(TaskStatus.SUPPORTED, f"accepted: grounded in {sources} source block(s), no blocking finding ({settings.profile} policy)")
    # ABSENT: not_found only when the required inspection is complete
    if material:
        return decision(TaskStatus.UNCERTAIN, "nothing was found, but absence cannot be claimed:")
    status = TaskStatus.NOT_FOUND if task.rules.on_absent == OnAbsent.NOT_FOUND else TaskStatus.UNCERTAIN
    why = proposal.reason or "no passage states the value"
    return decision(status, f"{why}; the inspection was complete ({settings.profile} policy)")
