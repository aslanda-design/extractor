"""S10 + S11 - THE VALIDATION AND ACCEPTANCE PIPELINE, start to finish, in one place.

Read `validate_and_accept` top to bottom: each numbered step is one call, and each call lives in
its own small module (named in the comment). The input is a PROPOSAL from S07; the output is the
engine's decision for the task (`supported | not_found | uncertain | failed`) with its reasons.

    proposal + evidence + documents
        |  1. structure     structure.py       is it the kind of value the task asked for?
        |  2. grounding     grounding.py       do the cited blocks exist, was the quote found, is the value in it?
        |  3. normalization numeric.py         scale words, digits vs words ("1.2 million (SAY ONE MILLION AND TWENTY THOUSAND)")
        |  4. consistency   consistency.py     dependencies accepted, redaction, discarded candidates
        |  5. coverage      coverage_check.py  risk register scored for relevance to THIS task (absence needs it)
        |  6. semantic      semantic.py        optional: a (bigger) model audits the single answer
        |  7. qualification (below)            optional: is this model + settings qualified on your corpus?
        |  8. classify      policy.py          the profile decides which findings block
        |  9. decide        policy.py          supported | not_found | uncertain | failed, with reasons and recovery hints
        v
    Validated (report + AcceptanceDecision + the model calls it made)

Cheap deterministic layers run first; the model layer runs last and only on an answer that would
otherwise be accepted. No model confidence and no retrieval score is ever used to accept.
"""

from dataclasses import dataclass, field

from ..adapters.base import VerifierAdapter
from ..config.runtime import AcceptanceSettings
from ..contracts.candidate import ModelCall, ProposalOutcome, ValueProposal
from ..contracts.coverage import CoverageLedger
from ..contracts.evidence import EvidencePackage
from ..contracts.passage import IndexedDocument
from ..contracts.task import TaskSpecification
from ..contracts.validation import AcceptanceDecision, Category, ValidationFinding, ValidationLayer, ValidationReport
from ..events import emit, trace
from .consistency import check_consistency
from .coverage_check import Scorer, check_coverage
from .findings import finding
from .grounding import check_grounding
from .numeric import check_normalization
from .policy import classify, decide
from .semantic import check_semantic
from .structure import check_structure


@dataclass
class Validated:
    report: ValidationReport
    decision: AcceptanceDecision
    calls: list[ModelCall] = field(default_factory=list)


def validate_and_accept(
    task: TaskSpecification,
    proposal: ValueProposal,
    package: EvidencePackage,
    documents: list[IndexedDocument],
    settings: AcceptanceSettings | None = None,
    *,
    scorer: Scorer | None = None,
    query: str = "",
    verifier: VerifierAdapter | None = None,
    accepted_tasks: set[str] | None = None,
    qualified: bool | None = None,
    retries: int = 1,
    ledger: CoverageLedger | None = None,
) -> Validated:
    settings = settings or AcceptanceSettings()
    findings: list[ValidationFinding] = []
    trace(task.id, "validate", "start")

    def layer(name: str, found: list[ValidationFinding]) -> None:
        findings.extend(found)
        emit("S10", f"{task.id}: {name}: " + (f"{len(found)} finding(s)" if found else "ok"), "warn" if found else "info")
        trace(task.id, "validate", "note", f"{task.id}: validation layer '{name}'", "warn" if found else "info", layer=name, findings=len(found))

    # 1. Structure: type, allowed label, cardinality.
    layer("structure", check_structure(task, proposal))

    # 2. Source grounding: the answer must point at the document.
    layer("source grounding", check_grounding(task, proposal, documents))

    # 3. Normalization: the canonical value must still mean what the quote says.
    layer("normalization", check_normalization(task, proposal))

    # 4. Consistency: dependencies, redaction, candidates thrown away on the way.
    layer("consistency", check_consistency(task, proposal, accepted_tasks))

    # 5. Coverage: for an absent answer, was every risk that could hide the value either irrelevant or inspected?
    layer("coverage", check_coverage(task, proposal, package, documents, scorer=scorer, query=query,
                                     relevance_ratio=settings.risk_relevance_ratio))

    # 6. Semantic support (optional): only for an answer that is about to be accepted, so the model is paid for once.
    calls: list[ModelCall] = []
    if settings.semantic_check and verifier is not None and proposal.outcome == ProposalOutcome.FOUND \
            and not classify(findings, settings).material:
        semantic, calls = check_semantic(task, proposal, package, verifier, retries, settings.think_semantic)
        layer("semantic support", semantic)

    # 7. Qualification (optional): a configuration nobody measured on your corpus cannot produce accepted answers.
    if settings.require_qualification and qualified is not True and proposal.outcome in (ProposalOutcome.FOUND, ProposalOutcome.ABSENT):
        layer("qualification", [finding(ValidationLayer.QUALIFICATION, Category.MODEL_NOT_QUALIFIED,
                                        "this model and these settings have no qualification record on your corpus "
                                        "(run `eval --acceptance --qualify`)", task.id)])

    # 8. The profile decides which findings block.
    report = classify(findings, settings)

    # 9. Decide.
    decision = decide(task, proposal, report, settings)
    emit("S11", f"{task.id}: {decision.status.value} ({decision.policy} policy)" + (f" - {decision.reasons[0]}" if decision.reasons else ""),
         {"supported": "ok", "not_found": "ok"}.get(decision.status.value, "warn"))
    trace(task.id, "decide", "done", outcome=decision.status.value, blocking=len(decision.blocking))
    return Validated(report=report, decision=decision, calls=calls)
