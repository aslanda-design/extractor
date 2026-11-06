"""Evaluation harness: run the engine over the corpus and compare with the labels.

Phase 0 baseline metric: per task, did the engine return the expected status,
and (for supported scalars) the expected value? Stage-specific metrics
(retrieval recall, record recall, ...) are added by the phase that needs them.
"""

from pathlib import Path

from ..adapters import build_adapters
from ..config import RuntimeConfig
from ..contracts import TaskResult
from ..contracts.common import StrictModel, TaskKind, TaskStatus
from ..pipeline import run_pipeline
from ..scope import DocumentRequest, build_manifest
from ..spec import compile_specification_file
from .corpus import Corpus, EvalCase


class TaskScore(StrictModel):
    case_id: str
    task_id: str
    expected_status: TaskStatus
    actual_status: TaskStatus
    status_match: bool
    value_match: bool | None = None  # None when no value comparison applies


class EvalReport(StrictModel):
    scores: list[TaskScore]

    @property
    def status_accuracy(self) -> float:
        return sum(s.status_match for s in self.scores) / len(self.scores) if self.scores else 0.0

    @property
    def value_accuracy(self) -> float | None:
        scored = [s.value_match for s in self.scores if s.value_match is not None]
        return sum(scored) / len(scored) if scored else None


def _norm(value: object) -> str:
    return str(value).strip().casefold()


def _score(case: EvalCase, result: TaskResult) -> TaskScore:
    expected = case.expected[result.task_id]
    value_match = None
    if result.kind != TaskKind.RECORDS and expected.status == TaskStatus.SUPPORTED:
        value_match = (
            result.status == TaskStatus.SUPPORTED and _norm(result.value) == _norm(expected.value)
        )
    return TaskScore(
        case_id=case.case_id,
        task_id=result.task_id,
        expected_status=expected.status,
        actual_status=result.status,
        status_match=result.status == expected.status,
        value_match=value_match,
    )


def run_corpus(corpus: Corpus, config: RuntimeConfig) -> EvalReport:
    adapters = build_adapters(config)
    scores: list[TaskScore] = []
    for case in corpus.cases:
        spec = compile_specification_file(corpus.root / case.spec)
        docs = [corpus.root / "documents" / name for name in case.documents]
        manifest = build_manifest(DocumentRequest.from_paths(docs))
        for result in run_pipeline(spec, manifest, adapters, config):
            scores.append(_score(case, result))
    return EvalReport(scores=scores)
