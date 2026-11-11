"""S12 - The recovery actions for value and interpretation tasks: which finding calls for which change.

Each action answers one concrete failure (ARCHITECTURE S12: "add recovery only when it addresses a concrete failure category"):

  retry_call       a model call failed            -> ask again in smaller calls (a transport failure is simply asked again)
  widen_evidence   evidence did not fit the budget -> gather the evidence again with a bigger budget, so the omitted passages are read
  deepen           candidates conflict, the quote is not in the source, a reference is unresolved, the judge disagreed
                                                   -> the next depth: wider retrieval and a model that reasons before answering
  escalate_model   the same situations             -> the same evidence, read by the stronger model of `models.escalation`

The first two COMPLETE an inspection the engine already knows is incomplete; they are on by default. The last two change how the
answer is produced; they are off until measured (`recovery.actions`, `eval --recovery`).

Findings recovery cannot act on are named too (`unrecoverable`), so the trail tells a person what would be needed.
"""

from dataclasses import dataclass
from typing import Callable

from ..config.runtime import RecoverySettings
from ..contracts.candidate import ProposalOutcome
from ..contracts.common import TaskKind
from ..contracts.task import TaskSpecification
from ..contracts.validation import Category as C
from .attempt import Attempt, RecoveryTools

GROUNDING = {C.QUOTE_NOT_IN_SOURCE, C.QUOTE_APPROXIMATE, C.VALUE_NOT_IN_QUOTE, C.NO_EVIDENCE, C.SOURCE_MISSING, C.UNRESOLVED_REFERENCE,
             C.SEMANTIC_UNSUPPORTED, C.NOT_NORMALIZED}
NEEDS = {  # blocking findings no action here can remove, and what would
    C.PARSE_RISK_RELEVANT: "a page could not be read well: re-OCR of that page is needed (not built: see PROGRESS, decision 8)",
    C.DEPENDENCY_NOT_ACCEPTED: "a task this one depends on was not accepted",
    C.REDACTED: "the value is redacted in the document",
    C.MODEL_NOT_QUALIFIED: "the model and settings are not qualified on the corpus",
}


@dataclass
class Planned:
    action: str
    reason: str  # the finding or outcome that calls for it
    change: str  # what will be done differently
    run: Callable[[], Attempt]


def plan_actions(task: TaskSpecification, attempt: Attempt, profile: str, settings: RecoverySettings, tools: RecoveryTools,
                 tried: set[str]) -> list[Planned]:
    """The actions that apply to this attempt, cheapest first, without the ones already tried or not allowed."""
    blocking = {f.category: f.message for f in attempt.blocking}
    outcome = attempt.proposal.outcome
    plans: list[Planned] = []

    failed_calls = [c for c in attempt.proposal.calls if c.reply.data is None]
    no_model = any(c.reply.failure == "no_model" for c in failed_calls)  # nothing is configured: asking again cannot help
    if (outcome == ProposalOutcome.FAILED or C.MODEL_CALL_FAILED in blocking or failed_calls) and not no_model:
        why = blocking.get(C.MODEL_CALL_FAILED) or (f"a model call failed: {failed_calls[0].reply.error}" if failed_calls else attempt.proposal.reason)
        if task.kind == TaskKind.VALUE:
            plans.append(Planned("retry_call", why, "asked again with at most 2 passages per call (smaller prompts, shorter answers)",
                                 lambda: tools.attempt(attempt.package, profile, {"extraction": {"passages_per_call": 2}}, False)))
        else:
            cut = any(c.reply.failure == "truncated" for c in failed_calls)
            changes = {"interpretation": {"think": False, "think_judge": False}} if cut else {}
            plans.append(Planned("retry_call", why, "asked again without the reasoning preamble (the answer was cut off)" if cut else "asked again",
                                 lambda: tools.attempt(attempt.package, profile, changes, False)))

    omitted = [g for g in attempt.package.gaps if g.lower().startswith("omitted") or "evidence budget" in g]  # retrieval found it, the budget left it out
    if C.CONTENT_OMITTED in blocking or omitted:
        factor = settings.evidence_budget_factor
        plans.append(Planned("widen_evidence", blocking.get(C.CONTENT_OMITTED) or omitted[0], f"evidence gathered again with {factor:g}x the token budget, so the omitted passages are read",
                             lambda: tools.attempt(tools.retrieve(profile, factor), profile, {}, False)))

    doubt = outcome == ProposalOutcome.UNCERTAIN or any(c in blocking for c in GROUNDING)
    reason = next((blocking[c] for c in GROUNDING if c in blocking), attempt.proposal.reason)
    deeper = settings.deeper_profile.get(profile)
    if doubt and deeper:
        plans.append(Planned("deepen", reason, f"retrieval and reading at the '{deeper}' depth instead of '{profile}'",
                             lambda: tools.attempt(tools.retrieve(deeper, 1.0), deeper, {}, False)))
    if doubt and tools.has_escalation:
        plans.append(Planned("escalate_model", reason, "the same evidence read by the escalation model (models.escalation)",
                             lambda: tools.attempt(attempt.package, profile, {}, True)))

    return [p for p in plans if p.action in settings.actions and p.action not in tried]


def unrecoverable(attempt: Attempt) -> list[str]:
    """Plain-language notes for blocking findings that no recovery action addresses."""
    return [f"recovery cannot act on '{f.category}': {NEEDS[f.category]}" for f in attempt.blocking if f.category in NEEDS]
