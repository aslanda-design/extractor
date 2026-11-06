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
            S07  extract a value                              STUB (phase 5)
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

from dataclasses import dataclass, field
from pathlib import Path

from ..adapters import AdapterSet, build_adapters
from ..artifacts import ArtifactStore
from ..config import RuntimeConfig
from ..config.runtime import RetrievalSettings
from ..contracts import CompiledSpecification, DocumentManifest, TaskResult, TaskStatus
from ..contracts.common import Completeness, TaskKind
from ..contracts.coverage import CoverageLedger
from ..contracts.evidence import EvidencePackage
from ..contracts.passage import IndexedDocument
from ..contracts.result import ExecutionMetadata
from ..contracts.task import TaskSpecification
from ..indexing.builder import IndexBuilder
from ..retrieval.retrieval_pipeline import gather_evidence
from ..retrieval.tools import build_retrieval_tools
from ..retrieval.types import RetrievalTools, request_from_task
from ..scope import DocumentRequest, build_manifest, resolve_task_documents
from ..spec import compile_specification_file

STAGE_VERSIONS = {"S01": "1", "S02": "1", "S03": "1", "S04": "1", "S06": "1"}


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


# --------------------------------------------------------------------------------------
# The engine
# --------------------------------------------------------------------------------------


def run_extraction(
    spec_path: Path, request: DocumentRequest, config: RuntimeConfig, profile: str | None = None
) -> list[TaskResult]:
    """Documents + extraction YAML in, one TaskResult per task out."""
    store = ArtifactStore(config.storage.artifact_dir)

    # S01. Scope: hash the documents, resolve relationships and precedence, keep the originals.
    manifest = build_manifest(request, store)

    # S02. Compile the YAML into normalized tasks and a dependency graph.
    spec = compile_specification_file(spec_path)

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
) -> list[TaskResult]:
    """One TaskResult per task, in declaration order (required tasks always get an entry).

    Without `indexed` (and `store`) the retrieval stage is skipped. `evidence_out`, when
    given, receives the EvidencePackage of every task that went through retrieval.
    """
    ctx = RunContext(
        spec=spec, manifest=manifest, adapters=adapters, config=config, indexed=indexed,
        tools=build_retrieval_tools(config, adapters, store) if indexed is not None and store else None,
        retrieval=config.retrieval.resolve(profile), profile=profile or config.retrieval.profile,
        execution=ExecutionMetadata(
            document_hashes={d.document_id: d.sha256 for d in manifest.documents},
            schema_hash=spec.schema_hash, runtime_fingerprint=config.fingerprint(),
            models=adapters.model_labels(), stage_versions=STAGE_VERSIONS))

    results = {task_id: process_task(spec.task(task_id), ctx) for task_id in spec.graph.order}
    if evidence_out is not None:
        evidence_out.update(ctx.evidence)
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

    # S07 / S08 / S09. Extract a value, interpret, or discover records.      STUB (phases 5, 7, 8)
    # S10. Validate the proposal.                                             STUB (phase 6)
    # S11. Accept it under the engine's policy.                               STUB (phase 6)
    # S12. Recover: retry the affected stage with a concrete improvement.     STUB (phase 9)
    notes.append("extraction stages S05 and S07-S12 are not implemented yet")

    # S13. The result.
    return TaskResult(
        task_id=task.id, kind=task.kind, status=TaskStatus.FAILED,
        completeness=Completeness.UNKNOWN if task.kind == TaskKind.RECORDS else None,
        diagnostics=notes, execution=ctx.execution)


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
