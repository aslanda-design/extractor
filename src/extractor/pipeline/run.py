"""End-to-end run. Phase 0/1: S01 and S02 are real, every later stage is a stub.

Each stage is one clearly marked step below. As phases are implemented, the
stub for that stage is replaced by a call into its own package:

    S03/S04 parse + index        -> (phase 2/3) not implemented
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
from ..contracts.result import ExecutionMetadata
from ..scope import DocumentRequest, build_manifest, resolve_task_documents
from ..spec import compile_specification_file

STAGE_VERSIONS = {"S01": "1", "S02": "1"}


def run_pipeline(
    spec: CompiledSpecification,
    manifest: DocumentManifest,
    adapters: AdapterSet,
    config: RuntimeConfig,
) -> list[TaskResult]:
    """Return one TaskResult per task, in declaration order (required fields always get one)."""
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

        # S03-S11: not implemented yet.
        diagnostics.append("extraction stages S03-S11 are not implemented yet")

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
    return run_pipeline(spec, manifest, build_adapters(config), config)
