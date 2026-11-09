"""THE WHOLE ENGINE, start to finish, in one place.

Read `run_extraction` (documents + spec in) and `process_task` (one task through
the stages) top to bottom. Each numbered step is a call into the package that
owns that stage; steps marked STUB are not built yet and report `failed`.

    run_extraction                                            owner
        S01  scope the documents                              scope/
        S02  compile the YAML                                 spec/
        S03  parse each document                              parsing/        (inside S04's builder)
        S04  chunk + index each document                      indexing/       chunking_pipeline.py
        for each task, in dependency order -> process_task
            S01  which documents may this task read?          scope/
            S05  plan                                         STUB (phase 9)
            S06  retrieve evidence                            retrieval/      retrieval_pipeline.py
            S07  extract a value                              extraction/     value_pipeline.py
            S08  interpret                                    STUB (phase 7)
            S09  discover records                             STUB (phase 8)
            S10  validate                                     STUB (phase 6)
            S11  accept                                       STUB (phase 6)
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
from ..contracts.common import Completeness, EvidenceRole, TaskKind
from ..contracts.coverage import CoverageLedger
from ..contracts.candidate import ProposalOutcome, ValueProposal
from ..contracts.evidence import EvidencePackage
from ..contracts.passage import IndexedDocument
from ..contracts.result import EvidenceLink, ExecutionMetadata
from ..contracts.task import OnAbsent, TaskSpecification
from ..events import emit, progress
from ..extraction import extract_value
from ..indexing.builder import IndexBuilder
from ..retrieval.retrieval_pipeline import gather_evidence
from ..retrieval.tools import build_retrieval_tools
from ..retrieval.types import RetrievalTools, request_from_task
from ..scope import DocumentRequest, build_manifest, resolve_task_documents
from ..spec import compile_specification_file

STAGE_VERSIONS = {"S01": "1", "S02": "1", "S03": "1", "S04": "1", "S06": "1", "S07": "1"}


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
    proposals: dict[str, ValueProposal] = field(default_factory=dict)


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
    ctx = RunContext(
        spec=spec, manifest=manifest, adapters=adapters, config=config, indexed=indexed,
        tools=build_retrieval_tools(config, adapters, store) if indexed is not None and store else None,
        retrieval=config.retrieval.resolve(profile), profile=profile or config.retrieval.profile,
        execution=ExecutionMetadata(
            document_hashes={d.document_id: d.sha256 for d in manifest.documents},
            schema_hash=spec.schema_hash, runtime_fingerprint=config.fingerprint(),
            models=adapters.model_labels(), stage_versions=STAGE_VERSIONS))

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


def process_task(task: TaskSpecification, ctx: RunContext) -> TaskResult:
    """One task through the stages."""
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

    # S05. Plan: grouping tasks that share evidence, budgets, concurrency.   STUB (phase 9)

    # S06. Retrieve evidence. Records are discovered over the whole scope in S09, not by retrieval.
    if ctx.tools is not None and ctx.indexed is not None and task.kind != TaskKind.RECORDS:
        documents = [ctx.indexed[d] for d in document_ids if d in ctx.indexed]
        package = gather_evidence(request_from_task(task, document_ids), documents, ctx.tools, ctx.retrieval,
                                  manifest=ctx.manifest, ledger=ctx.ledger, profile=ctx.profile)
        ctx.evidence[task.id] = package
        notes.append(f"evidence ({ctx.profile}): {len(package.passages)} passage(s), "
                     f"{package.trace.used_tokens}/{package.trace.budget_tokens} tokens, {len(package.gaps)} gap(s)")

    # S07. Extract a value (value tasks). S08 interpretation and S09 records come in phases 7 and 8.
    status, value, alternatives, evidence = TaskStatus.FAILED, None, [], []
    if task.id in ctx.evidence and task.kind == TaskKind.VALUE:
        proposal = extract_value(task, ctx.evidence[task.id], [ctx.indexed[d] for d in document_ids if d in ctx.indexed],
                                 ctx.adapters.extractor, ctx.config.extraction_for(ctx.profile), ledger=ctx.ledger)
        ctx.proposals[task.id] = proposal
        status, alternatives, evidence = _proposal_to_result(task, proposal, notes)
    else:
        notes.append("extraction stages S08 and S09 are not implemented yet" if task.kind != TaskKind.VALUE
                     else "no evidence was gathered, so nothing could be extracted")

    # S10. Validate the proposal.                                             STUB (phase 6)
    # S11. Accept it under the engine's policy.                               STUB (phase 6)
    # S12. Recover: retry the affected stage with a concrete improvement.     STUB (phase 9)
    notes.append("S05 and S10-S12 are not implemented yet: nothing is accepted as `supported`")

    # S13. The result.
    return TaskResult(
        task_id=task.id, kind=task.kind, status=status, alternatives=alternatives, evidence=evidence,
        completeness=Completeness.UNKNOWN if task.kind == TaskKind.RECORDS else None,
        diagnostics=notes, execution=ctx.execution)


def _proposal_to_result(task: TaskSpecification, proposal: ValueProposal, notes: list[str]):
    """Until validation and acceptance exist (phase 6), a proposal is never `supported`.

    A found value is offered as an alternative of an `uncertain` result; absence follows the
    task's `on_absent`; a failed call stays `failed`. The reason is always kept as a diagnostic.
    """
    notes.append(f"extraction ({proposal.outcome.value}): {proposal.reason}")
    notes += [f"gap: {g}" for g in proposal.gaps]
    evidence = [EvidenceLink(ref=ref, role=EvidenceRole.SUPPORTS, quote=c.quote)
                for c in proposal.chosen for ref in c.evidence]
    if proposal.outcome == ProposalOutcome.FOUND:
        notes.append("proposed value is not validated or accepted yet (phase 6)")
        return TaskStatus.UNCERTAIN, [proposal.value], evidence
    if proposal.outcome == ProposalOutcome.ABSENT:
        return (TaskStatus.NOT_FOUND if task.rules.on_absent == OnAbsent.NOT_FOUND else TaskStatus.UNCERTAIN), [], []
    if proposal.outcome == ProposalOutcome.UNCERTAIN:
        values = [c.normalized_value if c.normalized_value is not None else c.source_value for c in proposal.candidates]
        seen: dict[str, object] = {}
        for v in values:
            seen.setdefault(json.dumps(v, sort_keys=True, default=str), v)
        return TaskStatus.UNCERTAIN, list(seen.values()), []
    return TaskStatus.FAILED, [], []



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
