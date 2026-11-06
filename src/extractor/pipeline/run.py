"""End-to-end run. Phase 0/1: S01 and S02 are real, every later stage is a stub.

Each stage is one clearly marked step below. As phases are implemented, the
stub for that stage is replaced by a call into its own package:

    S03/S04 parse + index        -> implemented (phases 2/3): every document is parsed, chunked
                                    and indexed (cached) before the tasks run
    S05     plan                 -> (phase 9)   not implemented
    S06     retrieve evidence    -> (phase 4)   not implemented
    S07-S09 extraction strategies-> (phases 5,7,8) not implemented
    S10/S11 validate + accept    -> (phase 6)   not implemented

Until a task's strategy exists the task is reported as `failed` with a
diagnostic. It is never reported as `not_found`: a stage that did not run
proves nothing about the document.
"""

from pathlib import Path

from ..adapters import AdapterSet, build_adapters
from ..artifacts import ArtifactStore
from ..config import RuntimeConfig
from ..contracts import (
    CompiledSpecification,
    DocumentManifest,
    TaskResult,
    TaskStatus,
)
from ..contracts.common import Completeness, TaskKind
from ..contracts.passage import IndexedDocument
from ..contracts.result import ExecutionMetadata
from ..indexing.builder import IndexBuilder
from ..scope import DocumentRequest, build_manifest, resolve_task_documents
from ..spec import compile_specification_file

STAGE_VERSIONS = {"S01": "1", "S02": "1", "S03": "1", "S04": "1"}


def index_documents(
    manifest: DocumentManifest, adapters: AdapterSet, config: RuntimeConfig, store: ArtifactStore
) -> dict[str, IndexedDocument]:
    """S03 + S04 for every document in scope (cached by content and settings)."""
    builder = IndexBuilder(config, adapters, store)
    indexed = {}
    for entry in manifest.documents:
        if entry.document_id in manifest.scope.document_ids:
            indexed[entry.document_id] = builder.index(entry, store.original_path(entry.sha256, entry.format))
    return indexed


def run_pipeline(
    spec: CompiledSpecification,
    manifest: DocumentManifest,
    adapters: AdapterSet,
    config: RuntimeConfig,
    indexed: dict[str, IndexedDocument] | None = None,
) -> list[TaskResult]:
    """Return one TaskResult per task, in declaration order (required fields always get one).

    `indexed` is the output of `index_documents`; without it the S03/S04 stage is skipped.
    """
    execution = ExecutionMetadata(
        document_hashes={d.document_id: d.sha256 for d in manifest.documents},
        schema_hash=spec.schema_hash,
        runtime_fingerprint=config.fingerprint(),
        models=adapters.model_labels(),
        stage_versions=STAGE_VERSIONS,
    )

    results: dict[str, TaskResult] = {}
    for task_id in spec.graph.order:  # dependency order
        task = spec.task(task_id)
        diagnostics: list[str] = []

        # S01: which documents may this task read?
        document_ids = resolve_task_documents(task, manifest)
        if not manifest.scope.precedence_resolved and len(document_ids) > 1:
            diagnostics.append("several documents without relationships or precedence rules")
        diagnostics += [f"review: {i.message}" for i in spec.review_issues if i.task_id == task.id]

        # S03/S04 summary (the data itself is used by the later stages).
        if indexed is not None:
            docs = [indexed[d] for d in document_ids if d in indexed]
            passages = sum(len(d.passages) for d in docs)
            open_gaps = sum(len(d.coverage.unresolved()) for d in docs)
            diagnostics.append(f"indexed {len(docs)} document(s): {passages} passage(s), "
                               f"{open_gaps} unresolved parsing gap(s)")

        # S05-S11: not implemented yet.
        diagnostics.append("extraction stages S05-S11 are not implemented yet")

        results[task_id] = TaskResult(
            task_id=task.id,
            kind=task.kind,
            status=TaskStatus.FAILED,
            completeness=Completeness.UNKNOWN if task.kind == TaskKind.RECORDS else None,
            diagnostics=diagnostics,
            execution=execution,
        )
    return [results[t.id] for t in spec.tasks]


def run_files(
    spec_path: Path,
    request: DocumentRequest,
    config: RuntimeConfig,
) -> list[TaskResult]:
    """Convenience wrapper used by the CLI: files in, results out."""
    store = ArtifactStore(config.storage.artifact_dir)
    spec = compile_specification_file(spec_path)
    manifest = build_manifest(request, store)
    adapters = build_adapters(config, store)
    indexed = index_documents(manifest, adapters, config, store)
    return run_pipeline(spec, manifest, adapters, config, indexed)
