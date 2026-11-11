"""S09 - THE RECORDS PIPELINE, start to finish, in one place.

A records task asks for EVERY occurrence of something (the clauses, the milestones, the definitions...) inside a bounded scope. Top-k
retrieval cannot answer that: a missed record is invisible. So this stage reads the whole scope. Read `extract_records` top to bottom:
each numbered step is one call, and each call lives in its own small module (named in the comment).

    records task + the documents in its scope
        |  1. scope      scope.py      a model reads the document OUTLINE and names the region; the engine checks it (fallback: whole document)
        |  2. discover   discovery.py  the scope is read window by window: for every record its key, start block, first words, end block
        |  3. positions  spans.py      first words -> exact offsets; a record ends where the next one starts (it may cross a page)
        |  4. identity   spans.py      the key as printed, a record id derived from the source position
        |  5. fields     fields.py     per record: its own text is the ONLY evidence; one grouped call for the value fields, label fields
        |                              through S08; every field goes through S10/S11
        |  6. reconcile  spans.py      a record seen in two windows is one record; the same key in two places is a duplicate key
        |  7. audit      audit.py      numbering gaps, numbered text no record covers, truncated or failed windows, parsing problems in scope
        v
    RecordsOutcome (records with per-field results + audit findings); `validation/records.py` decides the task status and completeness

The model reports what is where. It never decides alone: a record exists because the engine found its first words in the block it named,
its fields are accepted by the same policy as any value, and "complete" is only said after the audit.
"""

from dataclasses import dataclass, field

from ..adapters.registry import AdapterSet
from ..config.runtime import RuntimeConfig
from ..contracts.candidate import ModelCall
from ..contracts.coverage import CoverageLedger
from ..contracts.passage import IndexedDocument
from ..contracts.record import Record
from ..contracts.task import TaskSpecification
from ..contracts.validation import Category, ValidationFinding, ValidationLayer
from ..validation.findings import finding
from ..events import emit, progress
from ..validation.coverage_check import Scorer
from .audit import audit
from .discovery import Discovery, discover
from .fields import FieldOutcome, extract_fields
from .keys import nested_keys
from .outline import tag_blocks
from .rejecting import reject_non_records
from .scope import Scope, find_scope
from .spans import Span, as_evidence, as_passage, build_spans, reconcile


@dataclass
class FoundRecord:
    record: Record
    span: Span
    outcomes: list[FieldOutcome] = field(default_factory=list)

    @property
    def accepted(self) -> bool:
        return all(o.accepted for o in self.outcomes)


@dataclass
class DocumentRead:
    """Everything S09 knows about ONE document: kept so recovery (S12) can read a region again and rebuild from here."""

    document: IndexedDocument
    tagged: list
    scope: Scope
    discovery: Discovery
    records: list[FoundRecord] = field(default_factory=list)
    findings: list[ValidationFinding] = field(default_factory=list)
    calls: list[ModelCall] = field(default_factory=list)
    log: list[str] = field(default_factory=list)


@dataclass
class RecordsOutcome:
    records: list[FoundRecord] = field(default_factory=list)
    findings: list[ValidationFinding] = field(default_factory=list)
    scopes: list[str] = field(default_factory=list)  # one line per document: where the records were searched
    scope_texts: list[str] = field(default_factory=list)  # the text of the scope region of each document (for the evaluation)
    calls: list[ModelCall] = field(default_factory=list)
    windows: int = 0
    discovery_failed: bool = False  # every discovery window of every document failed: nothing was read
    log: list[str] = field(default_factory=list)
    documents: list[DocumentRead] = field(default_factory=list)  # the per-document state behind the flat lists above


def rejection_findings(task_id: str, rejected, tagged) -> list[ValidationFinding]:
    out = [finding(ValidationLayer.RECORD_COMPLETENESS, Category.RECORD_REJECTED,
                   f"'{r.found.key}' (page {tagged[r.found.start].block.page}) was listed but does not match the record definition: {r.reason}", task_id)
           for r in rejected.dropped]
    if rejected.error:
        out.append(finding(ValidationLayer.RECORD_COMPLETENESS, Category.RECORD_REJECTED, f"the check of the listed records could not be used: {rejected.error}", task_id))
    return out


def extract_records(
    task: TaskSpecification,
    documents: list[IndexedDocument],
    adapters: AdapterSet,
    config: RuntimeConfig,
    profile: str | None = None,
    *,
    scorer: Scorer | None = None,
    ledger: CoverageLedger | None = None,
) -> RecordsOutcome:
    settings = config.records
    model = adapters.extractor
    reads: list[DocumentRead] = []
    skipped: list[str] = []
    for document in documents:
        blocks = document.representation.blocks
        if not blocks:
            skipped.append(f"{document.document_id}: no blocks (nothing was parsed)")
            continue
        tagged = tag_blocks(blocks)

        # 1. Scope: which blocks may hold the records?
        scope: Scope = find_scope(task, tagged, model, settings)
        emit("S09", f"{task.id}: scope {scope.how}", "warn" if scope.fell_back else "info")

        # 2. Discover the boundaries over the whole scope (window by window).
        discovery: Discovery = discover(task, tagged, scope, model, settings)

        # 3-7. From what the model reported to records with fields, then the audit.
        reads.append(read_document(task, document, tagged, scope, discovery, adapters, config, profile, scorer=scorer, ledger=ledger))
    return assemble(reads, skipped)


def read_document(
    task: TaskSpecification, document: IndexedDocument, tagged: list, scope: Scope, discovery: Discovery,
    adapters: AdapterSet, config: RuntimeConfig, profile: str | None = None, *,
    scorer: Scorer | None = None, ledger: CoverageLedger | None = None, previous: list[FoundRecord] | None = None,
) -> DocumentRead:
    """Steps 3 to 7 for one document, from a discovery. `previous` are the records of an earlier read of the same document:
    a record whose span did not change keeps its fields (recovery reads a region again without paying for the rest)."""
    settings = config.records
    read = DocumentRead(document=document, tagged=tagged, scope=scope, discovery=discovery, calls=[*scope.calls, *discovery.calls])

    # 3 + 4 + 6. Positions, identity, duplicates: from what the model reported to records with exact spans.
    found, duplicates = reconcile(discovery.found, tagged)

    # 2a. Sub-items of a listed record are part of it, not records of their own ("3.1" under "3").
    nested = nested_keys([f.key for f in found])
    read.findings += [finding(ValidationLayer.RECORD_COMPLETENESS, Category.RECORD_REJECTED,
                              f"'{f.key}' is a sub-item of another listed record, not a record of its own", task.id) for i, f in enumerate(found) if i in nested]
    found = [f for i, f in enumerate(found) if i not in nested]

    # 2b. A second look: entries that do not match the record definition leave the collection (and are reported).
    rejected = reject_non_records(task, found, tagged, adapters.extractor, settings)
    found = rejected.kept
    if rejected.call:
        read.calls.append(rejected.call)
    read.findings += rejection_findings(task.id, rejected, tagged)
    read.findings += [finding(ValidationLayer.RECORD_COMPLETENESS, Category.RECORD_REJECTED, why, task.id) for why in discovery.wandered]
    spans = build_spans(document.document_id, task.id, found, tagged)
    read.log.append(f"{document.document_id}: {len(discovery.found)} record report(s) -> {len(spans)} record(s)"
                    + (f"; duplicate key: {', '.join(duplicates)}" if duplicates else ""))

    # 5. Fields of each record, from the record's own text.
    known = {(r.span.record_id, r.span.text): r for r in previous or []}
    for n, span in enumerate(spans, start=1):
        same = known.get((span.record_id, span.text))
        if same is not None:  # read before, unchanged: its fields stand
            read.records.append(FoundRecord(same.record, span, same.outcomes))
            continue
        passage = as_passage(span, document.document_id, tagged)
        outcomes = extract_fields(task, span.record_id, passage, as_evidence(passage), document, adapters, config, profile, scorer, ledger)
        read.calls += [c for o in outcomes for c in o.calls]
        read.records.append(FoundRecord(make_record(span, outcomes), span, outcomes))
        progress("S09", n, len(spans), f"records of {task.id} have their fields")

    # 7. Audit: was the whole scope accounted for?
    read.findings += audit(task.id, document, tagged, scope, discovery, spans, duplicates, settings.check_numbering)
    return read


def make_record(span: Span, outcomes: list[FieldOutcome]) -> Record:
    return Record(record_id=span.record_id, key=span.key, text=span.text, block_ids=span.block_ids, fields=[o.result for o in outcomes])


def assemble(reads: list[DocumentRead], skipped: list[str] | None = None) -> RecordsOutcome:
    """The flat outcome of a task from its per-document reads."""
    out = RecordsOutcome(documents=reads, log=list(skipped or []))
    for read in reads:
        out.records += read.records
        out.findings += read.findings
        out.calls += read.calls
        out.windows += read.discovery.windows
        out.scopes.append(f"{read.document.document_id}: {read.scope.how}")
        out.scope_texts.append(" ".join(t.block.text for t in read.tagged[read.scope.first:read.scope.last + 1]))
        out.log += read.log
    windows = [r.discovery for r in reads if r.discovery.windows > 0]
    # nothing parsed is not "no records": it is "nothing was read". Same when every window of a document failed.
    out.discovery_failed = not reads or any(len(d.failed_windows) == d.windows for d in windows)
    return out
