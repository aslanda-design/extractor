"""THE WHOLE ENGINE, start to finish, in one place.

Read `run_extraction` (documents + spec in) and `process_task` (one task through
the stages) top to bottom. Each numbered step is a call into the package that
owns that stage; steps marked STUB are not built yet and report `failed`.

    run_extraction                                            owner
        S01  scope the documents                              scope/
        S02  compile the YAML                                 spec/
        S03  parse each document                              parsing/        (inside S04's builder)
        S04  chunk + index each document                      indexing/       chunking_pipeline.py
        run_pipeline
            for each task: S01 which documents may it read? + S06 retrieve evidence    scope/, retrieval/ (retrieval_pipeline.py)
            S05  plan which tasks share a model call          planning/       planning_pipeline.py
            S07  ask the model, once per batch of the plan    extraction/     value_pipeline.py (ask_batch)
        for each task, in dependency order -> process_task
            S07  conclude: from the answer to one proposal    extraction/     value_pipeline.py (conclude_task)
            S08  interpret: decide a label under criteria     interpretation/ interpretation_pipeline.py
            S09  discover records, extract their fields        records/ records_pipeline.py
            S10  validate                                     validation/     validation_pipeline.py
            S11  accept                                       validation/     (same file: policy.py)
            S12  recover (retry what failed)                  STUB (phase 9)
            S13  result                                       contracts/result.py

A stage that did not run is reported as `failed` with a diagnostic, never as
`not_found`: it proves nothing about the document. Every pipeline in the
engine follows this layout: one `*_pipeline.py` file, numbered steps, helpers
in their own modules. When a phase is built, its STUB line becomes a real call.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..adapters import AdapterSet, build_adapters
from ..artifacts import ArtifactStore
from ..config import RuntimeConfig
from ..config.runtime import RetrievalSettings
from ..contracts import CompiledSpecification, DocumentManifest, TaskResult, TaskStatus
from ..contracts.common import Completeness, EvidenceRole, SourceRef, TaskKind
from ..contracts.coverage import CoverageLedger
from ..contracts.candidate import ProposalOutcome, ValueProposal
from ..contracts.evidence import EvidencePackage
from ..contracts.plan import ExtractionPlan
from ..contracts.passage import IndexedDocument
from ..contracts.result import EvidenceLink, ExecutionMetadata
from ..contracts.validation import ValidationReport
from ..contracts.task import OnAbsent, TaskSpecification
from ..events import emit, progress
from ..extraction import Asked, ask_batch, ask_task, conclude_task
from ..interpretation.interpretation_pipeline import interpret_task
from ..records.records_pipeline import extract_records
from ..validation import Validated, validate_and_accept
from ..validation.records import decide_records
from ..validation.qualification import is_qualified, qualification_key
from ..retrieval.queries import build_queries
from ..indexing.builder import IndexBuilder
from ..planning import plan_extraction
from ..retrieval.retrieval_pipeline import gather_evidence
from ..retrieval.tools import build_retrieval_tools
from ..retrieval.types import RetrievalTools, request_from_task
from ..scope import DocumentRequest, build_manifest, resolve_task_documents
from ..spec import compile_specification_file

STAGE_VERSIONS = {"S01": "1", "S02": "1", "S03": "1", "S04": "1", "S05": "1", "S06": "1", "S07": "4", "S10": "1", "S11": "1"}


@dataclass
class RunContext:
    """Everything the stages share during one run."""

    spec: CompiledSpecification
    manifest: DocumentManifest
    adapters: AdapterSet
    config: RuntimeConfig
    indexed: dict[str, IndexedDocument] | None
    tools: RetrievalTools | None
    retrieval: RetrievalSettings
    profile: str
    execution: ExecutionMetadata
    ledger: CoverageLedger = field(default_factory=CoverageLedger)
    evidence: dict[str, EvidencePackage] = field(default_factory=dict)
    plan: ExtractionPlan | None = None
    asked: dict[str, Asked] = field(default_factory=dict)  # what the model said, per value task (S07 steps 1-4)
    proposals: dict[str, ValueProposal] = field(default_factory=dict)
    decisions: dict[str, Validated] = field(default_factory=dict)
    accepted: set[str] = field(default_factory=set)  # tasks whose answer was accepted, for depends_on checks
    store: ArtifactStore | None = None


# --------------------------------------------------------------------------------------
# The engine
# --------------------------------------------------------------------------------------


def run_extraction(
    spec_path: Path, request: DocumentRequest, config: RuntimeConfig, profile: str | None = None
) -> list[TaskResult]:
    """Documents + extraction YAML in, one TaskResult per task out."""
    store = ArtifactStore(config.storage.artifact_dir)

    # S01. Scope: hash the documents, resolve relationships and precedence, keep the originals.
    emit("S01", f"scoping {len(request.documents)} document(s)")
    manifest = build_manifest(request, store)
    emit("S01", f"manifest ready: {len(manifest.documents)} document(s), {len(manifest.scope.document_ids)} in scope", "ok")

    # S02. Compile the YAML into normalized tasks and a dependency graph.
    spec = compile_specification_file(spec_path)
    emit("S02", f"specification compiled: {len(spec.tasks)} task(s)", "ok")

    # S03 + S04. Parse, chunk and index every document (each step cached by content and settings).
    adapters = build_adapters(config, store)
    indexed = index_documents(manifest, adapters, config, store)

    # Then every task, dependencies first.
    return run_pipeline(spec, manifest, adapters, config, indexed, store=store, profile=profile)


def _context(
    spec: CompiledSpecification, manifest: DocumentManifest, adapters: AdapterSet, config: RuntimeConfig,
    indexed: dict[str, IndexedDocument] | None, store: ArtifactStore | None, profile: str | None,
) -> RunContext:
    return RunContext(
        spec=spec, manifest=manifest, adapters=adapters, config=config, indexed=indexed,
        tools=build_retrieval_tools(config, adapters, store) if indexed is not None and store else None,
        retrieval=config.retrieval.resolve(profile), profile=profile or config.retrieval.profile, store=store,
        execution=ExecutionMetadata(
            document_hashes={d.document_id: d.sha256 for d in manifest.documents},
            schema_hash=spec.schema_hash, runtime_fingerprint=config.fingerprint(),
            models=adapters.model_labels(), stage_versions=STAGE_VERSIONS))


def plan_only(
    spec: CompiledSpecification, manifest: DocumentManifest, adapters: AdapterSet, config: RuntimeConfig,
    indexed: dict[str, IndexedDocument], store: ArtifactStore, profile: str | None = None,
) -> RunContext:
    """S06 + S05 only (no chat model): the evidence of every task and the plan made from it. For the UI and the CLI."""
    ctx = _context(spec, manifest, adapters, config, indexed, store, profile)
    retrieve_all(spec, ctx)
    return ctx


def run_pipeline(
    spec: CompiledSpecification,
    manifest: DocumentManifest,
    adapters: AdapterSet,
    config: RuntimeConfig,
    indexed: dict[str, IndexedDocument] | None = None,
    *,
    store: ArtifactStore | None = None,
    profile: str | None = None,
    evidence_out: dict[str, EvidencePackage] | None = None,
    proposals_out: dict[str, ValueProposal] | None = None,
) -> list[TaskResult]:
    """One TaskResult per task, in declaration order (required tasks always get an entry).

    Without `indexed` (and `store`) the retrieval stage is skipped. `evidence_out`, when
    given, receives the EvidencePackage of every task that went through retrieval;
    `proposals_out` the ValueProposal of every value task that was extracted.
    """
    ctx = _context(spec, manifest, adapters, config, indexed, store, profile)

    # S06 for every task first (no model involved), then S05 looks at all the evidence at once.
    retrieve_all(spec, ctx)
    ask_all(spec, ctx)

    results = {}
    for n, task_id in enumerate(spec.graph.order, start=1):
        emit("run", f"task {n}/{len(spec.graph.order)}: {task_id} ({spec.task(task_id).kind.value})")
        results[task_id] = process_task(spec.task(task_id), ctx)
        progress("run", n, len(spec.graph.order), "tasks done")
        emit("S13", f"{task_id}: {results[task_id].status.value}",
             {"supported": "ok", "not_found": "info", "uncertain": "warn", "failed": "error"}[results[task_id].status.value])
    if evidence_out is not None:
        evidence_out.update(ctx.evidence)
    if proposals_out is not None:
        proposals_out.update(ctx.proposals)
    return [results[t.id] for t in spec.tasks]


def retrieve_all(spec: CompiledSpecification, ctx: RunContext) -> None:
    """S06 for every value / interpretation task, then S05: the plan made from all the evidence."""
    if ctx.tools is None or ctx.indexed is None:
        return  # no index: the retrieval stage is skipped
    retrievable = [t for t in spec.graph.order if spec.task(t).kind != TaskKind.RECORDS]  # records: whole scope, S09
    for n, task_id in enumerate(retrievable, start=1):
        task = spec.task(task_id)
        document_ids = resolve_task_documents(task, ctx.manifest)
        documents = [ctx.indexed[d] for d in document_ids if d in ctx.indexed]
        ctx.evidence[task_id] = gather_evidence(request_from_task(task, document_ids), documents, ctx.tools, ctx.retrieval,
                                                manifest=ctx.manifest, ledger=ctx.ledger, profile=ctx.profile)
        progress("S06", n, len(retrievable), "tasks have their evidence")
    values = {t: ctx.evidence[t] for t in retrievable if spec.task(t).kind == TaskKind.VALUE}
    if values:
        ctx.plan = plan_extraction(values, {t: spec.task(t) for t in values}, ctx.config.planning,
                                   ctx.config.extraction_for(ctx.profile), ctx.adapters.extractor)


def ask_all(spec: CompiledSpecification, ctx: RunContext) -> None:
    """S07 steps 1-4: one model call per batch of the plan (a batch is one task, or several that share evidence)."""
    if ctx.plan is None or ctx.indexed is None:
        return
    batches = ctx.plan.batches
    for n, batch in enumerate(batches, start=1):
        tasks = [spec.task(t) for t in batch.task_ids]
        document_ids = list(dict.fromkeys(d for t in tasks for d in resolve_task_documents(t, ctx.manifest)))
        ctx.asked.update(ask_batch(tasks, ctx.evidence, [ctx.indexed[d] for d in document_ids if d in ctx.indexed], ctx.adapters.extractor,
                                   ctx.config.extraction_for(ctx.profile), ctx.config.planning, ledger=ctx.ledger))
        progress("S07", n, len(batches), "extraction calls done")


def process_task(task: TaskSpecification, ctx: RunContext) -> TaskResult:
    """One task through the stages (its evidence was gathered by `retrieve_all`)."""
    notes: list[str] = []

    # S01. Which documents may this task read?
    document_ids = resolve_task_documents(task, ctx.manifest)
    if not ctx.manifest.scope.precedence_resolved and len(document_ids) > 1:
        notes.append("several documents without relationships or precedence rules")
    notes += [f"review: {i.message}" for i in ctx.spec.review_issues if i.task_id == task.id]

    # S03 / S04 summary: what is available to this task.
    if ctx.indexed is not None:
        docs = [ctx.indexed[d] for d in document_ids if d in ctx.indexed]
        notes.append(f"indexed {len(docs)} document(s): {sum(len(d.passages) for d in docs)} passage(s), "
                     f"{sum(len(d.coverage.unresolved()) for d in docs)} unresolved parsing gap(s)")

    # S05. Plan: which tasks share a model call (made once for all tasks in `retrieve_all`).
    # S06. Retrieve evidence (also done in `retrieve_all`; here only its summary). Records are discovered in S09, not by retrieval.
    if task.id in ctx.evidence:
        package = ctx.evidence[task.id]
        notes.append(f"evidence ({ctx.profile}): {len(package.passages)} passage(s), "
                     f"{package.trace.used_tokens}/{package.trace.budget_tokens} tokens, {len(package.gaps)} gap(s)")

    # S09. Records: the whole scope is read (no retrieval), then S10/S11 decide the collection.
    if task.kind == TaskKind.RECORDS and ctx.indexed is not None:
        return _process_records(task, ctx, document_ids, notes)

    # S07 / S08. Extract a value (value tasks) or decide a label under criteria (interpretation tasks).
    status, value, alternatives, evidence, validation, acceptance = TaskStatus.FAILED, None, [], [], ValidationReport(), None
    if task.id in ctx.evidence and task.kind in (TaskKind.VALUE, TaskKind.INTERPRETATION):
        scope_docs = [ctx.indexed[d] for d in document_ids if d in ctx.indexed]
        settings = ctx.config.extraction_for(ctx.profile)
        if task.kind == TaskKind.VALUE:
            asked = ctx.asked.get(task.id) or ask_task(task, ctx.evidence[task.id], scope_docs, ctx.adapters.extractor, settings, ledger=ctx.ledger)
            proposal = conclude_task(asked, ctx.adapters.extractor, settings)
            if asked.shared_with:
                notes.append(f"extraction call shared with {', '.join(asked.shared_with)}")
        else:
            proposal = interpret_task(task, ctx.evidence[task.id], scope_docs, ctx.adapters.interpretation_model,
                                      ctx.config.interpretation_for(ctx.profile), ledger=ctx.ledger)
        ctx.proposals[task.id] = proposal
        notes.append(f"{'extraction' if task.kind == TaskKind.VALUE else 'interpretation'} ({proposal.outcome.value}): {proposal.reason}")

        # S10 + S11. Validate the proposal and apply the engine's acceptance policy.
        query = build_queries(request_from_task(task, document_ids), ctx.retrieval).rerank
        qualified = is_qualified(ctx.store, qualification_key(ctx.config, ctx.adapters, ctx.profile)) \
            if ctx.config.acceptance.require_qualification else None
        validated = validate_and_accept(
            task, proposal, ctx.evidence[task.id], scope_docs, ctx.config.acceptance,
            scorer=(lambda q, texts: ctx.tools.reranker.score(q, texts)) if ctx.tools else None, query=query,
            verifier=ctx.adapters.verifier, accepted_tasks=ctx.accepted, qualified=qualified,
            retries=ctx.config.extraction_for(ctx.profile).retries, ledger=ctx.ledger)
        ctx.decisions[task.id] = validated
        status, value, alternatives, evidence = _result_parts(proposal, validated)
        validation, acceptance = validated.report, validated.decision
        if status == TaskStatus.SUPPORTED:
            ctx.accepted.add(task.id)
        notes += [f"{status.value}: {why}" for why in validated.decision.reasons[:3]]
        notes += [f"info: {f.message}" for f in validated.report.findings if f.severity.value == "info"][:5]
    else:
        notes.append("records need indexed documents (S04)" if task.kind == TaskKind.RECORDS
                     else "no evidence was gathered, so nothing could be extracted")

    # S12. Recover: retry the affected stage with a concrete improvement.     STUB (phase 9)
    #      Phase 6 already records what recovery could do: `acceptance.hints` (reparse a region, inspect omitted passages...).
    notes.append("S05 and S12 are not implemented yet")

    # S13. The result.
    return TaskResult(
        task_id=task.id, kind=task.kind, status=status, value=value, alternatives=alternatives, evidence=evidence,
        interpretation=ctx.proposals[task.id].interpretation if task.id in ctx.proposals else None,
        completeness=Completeness.UNKNOWN if task.kind == TaskKind.RECORDS else None,
        validation=validation, acceptance=acceptance, diagnostics=notes, execution=ctx.execution)


def _process_records(task: TaskSpecification, ctx: RunContext, document_ids: list[str], notes: list[str]) -> TaskResult:
    """S09 + S10/S11 for one records task."""
    docs = [ctx.indexed[d] for d in document_ids if d in ctx.indexed]
    outcome = extract_records(task, docs, ctx.adapters, ctx.config, ctx.profile,
                              scorer=(lambda q, texts: ctx.tools.reranker.score(q, texts)) if ctx.tools else None, ledger=ctx.ledger)
    report, decision, completeness = decide_records(task, outcome, ctx.config.acceptance)
    if decision.status == TaskStatus.SUPPORTED:
        ctx.accepted.add(task.id)
    notes += [*outcome.log, f"{decision.status.value}: {decision.reasons[0] if decision.reasons else ''}"]
    return TaskResult(
        task_id=task.id, kind=task.kind, status=decision.status, records=[r.record for r in outcome.records], completeness=completeness,
        evidence=[EvidenceLink(ref=SourceRef(document_id=b.rsplit(":p", 1)[0], block_id=b), role=EvidenceRole.SUPPORTS) for r in outcome.records for b in r.record.block_ids[:1]],
        validation=report, acceptance=decision, diagnostics=notes, scope_note=" | ".join(outcome.scopes), execution=ctx.execution)


def _result_parts(proposal: ValueProposal, validated: Validated):
    """(status, value, alternatives, evidence) from the engine's decision.

    Only an accepted answer becomes `value`. A found-but-blocked value, or the conflicting candidates of an
    uncertain proposal, are offered as `alternatives`; a failure or a complete absence carries none.
    """
    status = validated.decision.status
    chosen_evidence = [EvidenceLink(ref=ref, role=EvidenceRole.SUPPORTS, quote=c.quote) for c in proposal.chosen for ref in c.evidence]
    if proposal.interpretation and proposal.outcome == ProposalOutcome.FOUND:  # quotes that point at another label are kept, as contradicting
        chosen_evidence += [EvidenceLink(ref=ref, role=EvidenceRole.CONTRADICTS, quote=s.quote)
                            for s in proposal.interpretation.stances if s.label != proposal.value for ref in s.evidence]
    if status == TaskStatus.SUPPORTED:
        return status, proposal.value, [], chosen_evidence
    if proposal.outcome == ProposalOutcome.FOUND:
        return status, None, [proposal.value], chosen_evidence
    if proposal.outcome == ProposalOutcome.UNCERTAIN:
        live = [c for c in proposal.candidates if not any(f.startswith("rejected:") for f in c.flags)]
        seen: dict[str, object] = {}
        for c in live:
            v = c.normalized_value if c.normalized_value is not None else c.source_value
            seen.setdefault(json.dumps(v, sort_keys=True, default=str), v)
        return status, None, list(seen.values()), []
    return status, None, [], []


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------


def index_documents(
    manifest: DocumentManifest, adapters: AdapterSet, config: RuntimeConfig, store: ArtifactStore
) -> dict[str, IndexedDocument]:
    """S03 + S04 for every document in scope."""
    builder = IndexBuilder(config, adapters, store)
    return {
        entry.document_id: builder.index(entry, store.original_path(entry.sha256, entry.format))
        for entry in manifest.documents
        if entry.document_id in manifest.scope.document_ids
    }


def run_files(
    spec_path: Path, request: DocumentRequest, config: RuntimeConfig, profile: str | None = None
) -> list[TaskResult]:
    """File-in / results-out wrapper used by the CLI and the UI."""
    return run_extraction(spec_path, request, config, profile)
