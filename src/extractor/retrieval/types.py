"""Shared types for S06: what we are looking for, where we look, and what we look with."""

from dataclasses import dataclass, field
from typing import Callable

from ..adapters.base import EmbedderAdapter, RerankerAdapter
from ..contracts.common import TaskKind
from ..contracts.manifest import DocumentManifest
from ..contracts.passage import IndexedDocument, Passage
from ..contracts.task import FieldSpec, LabelSpec, TaskSpecification
from ..indexing.stores import LexicalIndex, VectorStore


class RetrievalError(ValueError):
    """The request cannot be served by evidence retrieval."""


@dataclass
class RetrievalRequest:
    """Everything retrieval needs to know about the job, independent of the YAML shape."""

    task_id: str
    kind: TaskKind
    description: str
    entity: str | None = None
    time: str | None = None
    labels: list[LabelSpec] = field(default_factory=list)
    criteria: list[str] = field(default_factory=list)  # decision criteria (interpretation)
    hints: list[str] = field(default_factory=list)  # extra terms: evidence requirements, precedence...
    document_ids: list[str] | None = None  # None = every document handed to retrieval
    passage_ids: set[str] | None = None  # restrict to these passages (the scope of one record)


def request_from_task(task: TaskSpecification, document_ids: list[str] | None = None) -> RetrievalRequest:
    if task.kind == TaskKind.RECORDS:
        raise RetrievalError(
            f"task '{task.id}' is a records task: records are discovered over the whole scope (S09), "
            "not by retrieval. Call retrieval for its fields instead (request_from_field).")
    return RetrievalRequest(
        task_id=task.id, kind=task.kind, description=task.description,
        entity=task.scope.entity, time=task.scope.time, labels=list(task.output.labels),
        criteria=list(task.rules.decision_criteria),
        hints=[*task.rules.evidence_requirements, *task.rules.precedence],
        document_ids=document_ids)


def request_from_field(
    field_spec: FieldSpec, record_task: TaskSpecification, passage_ids: set[str] | None = None,
    document_ids: list[str] | None = None,
) -> RetrievalRequest:
    """A field inside one record: same machinery, restricted to that record's passages."""
    return RetrievalRequest(
        task_id=f"{record_task.id}.{field_spec.id}", kind=field_spec.kind,
        description=f"{field_spec.description} (in the context of: {record_task.description})",
        entity=record_task.scope.entity, time=record_task.scope.time,
        labels=list(field_spec.output.labels), criteria=list(field_spec.rules.decision_criteria),
        hints=[*field_spec.rules.evidence_requirements, *field_spec.rules.precedence],
        document_ids=document_ids, passage_ids=passage_ids)


@dataclass
class RetrievalTools:
    """The models and indexes retrieval works with (all replaceable through the runtime config)."""

    lexical: LexicalIndex
    vectors: VectorStore
    embedder: EmbedderAdapter
    reranker: RerankerAdapter

    @property
    def count_tokens(self) -> Callable[[str], int]:
        return self.embedder.count_tokens


@dataclass
class Corpus:
    """The passages one request may read, in reading order across documents."""

    docs: dict[str, IndexedDocument]
    passages: dict[str, Passage]
    order: list[str]  # passage ids in reading order
    manifest: DocumentManifest | None = None

    @classmethod
    def build(
        cls, documents: list[IndexedDocument], request: RetrievalRequest, manifest: DocumentManifest | None = None
    ) -> "Corpus":
        wanted = set(request.document_ids) if request.document_ids is not None else None
        docs = {d.document_id: d for d in documents if wanted is None or d.document_id in wanted}
        passages, order = {}, []
        for doc in docs.values():
            for p in doc.passages:
                if request.passage_ids is None or p.passage_id in request.passage_ids:
                    passages[p.passage_id] = p
                    order.append(p.passage_id)
        return cls(docs, passages, order, manifest)

    def position(self, passage_id: str) -> int:
        return self._positions[passage_id]

    def __post_init__(self):
        self._positions = {pid: i for i, pid in enumerate(self.order)}

    def document_of(self, passage_id: str) -> IndexedDocument:
        return self.docs[self.passages[passage_id].document_id]

    def relationship_kinds(self, document_id: str) -> set[str]:
        if self.manifest is None:
            return set()
        try:
            return {r.kind.value for r in self.manifest.entry(document_id).relationships}
        except KeyError:
            return set()
