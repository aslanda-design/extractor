"""THE WHOLE ENGINE, start to finish, in one place.

Read `run_extraction` (documents + spec in) and `process_task` (one task through
the stages) top to bottom. Each numbered step is a call into the package that
owns that stage.

    run_extraction                                            owner
        S01  scope the documents                              scope/
        S02  compile the YAML                                 spec/
        S03  parse each document                              parsing/        (inside S04's builder)
        S04  chunk + index each document                      indexing/       chunking_pipeline.py
        run_pipeline_report                                   (one execution scope: call cache + run meter, execution/)
            for each task: S01 which documents may it read? + S06 retrieve evidence    scope/, retrieval/ (retrieval_pipeline.py)
            S05  plan which tasks share a model call          planning/       planning_pipeline.py
            S07  ask the model, once per batch of the plan    extraction/     value_pipeline.py (ask_batch)
        for each dependency layer, its tasks side by side (execution/scheduler.py) -> process_task
            S07  conclude: from the answer to one proposal    extraction/     value_pipeline.py (conclude_task)
            S08  interpret: decide a label under criteria     interpretation/ interpretation_pipeline.py
            S09  discover records, extract their fields       records/        records_pipeline.py
            S10  validate                                     validation/     validation_pipeline.py
            S11  accept                                       validation/     (same file: policy.py)
            S12  recover: bounded, targeted second tries      recovery/       recovery_pipeline.py
            S13  result                                       contracts/result.py (TaskResult, RunReport)

A stage that did not run is reported as `failed` with a diagnostic, never as
`not_found`: it proves nothing about the document. Every pipeline in the
engine follows this layout: one `*_pipeline.py` file, numbered steps, helpers
in their own modules.
"""

import json
import threading
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path

from .. import __version__
from ..adapters import AdapterSet, build_adapters
from ..artifacts.fingerprint import fingerprint
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
from ..contracts.result import EvidenceLink, ExecutionMetadata, RecoveryAttempt, RunReport, RunSummary
from ..contracts.validation import AcceptanceDecision, Category, ValidationLayer, ValidationReport
from ..contracts.task import TaskSpecification
from ..events import emit, progress, trace
from ..execution import Execution, execution_scope, parallel_limit, run_parallel, task_scope
from ..extraction import Asked, ask_batch, ask_task, conclude_task
from ..interpretation.interpretation_pipeline import interpret_task
from ..records.discovery import discover
from ..records.fields import extract_fields
from ..records.records_pipeline import extract_records, read_document
from ..records.spans import as_evidence, as_passage
from ..recovery import Attempt, RecordsAttempt, RecordsTools, RecoveryTools, recover_records, recover_task
from ..validation import Validated, validate_and_accept
from ..validation.findings import finding
from ..validation.policy import classify
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

STAGE_VERSIONS = {"S01": "1", "S02": "1", "S03": "1", "S04": "1", "S05": "1", "S06": "1", "S07": "5", "S08": "1", "S09": "1",
                  "S10": "1", "S11": "1", "S12": "1", "S13": "1"}
DECIDED = (TaskStatus.SUPPORTED, TaskStatus.NOT_FOUND)


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
    statuses: dict[str, TaskStatus] = field(default_factory=dict)  # the status of every finished task (a dependent inherits a failure)
    store: ArtifactStore | None = None
    exe: Execution | None = None  # the call cache and the run meter of this run
    phase_seconds: dict[str, float] = field(default_factory=dict)


# --------------------------------------------------------------------------------------
# The engine
# --------------------------------------------------------------------------------------


def run_extraction(
    spec_path: Path, request: DocumentRequest, config: RuntimeConfig, profile: str | None = None
) -> list[TaskResult]:
    """Documents + extraction YAML in, one TaskResult per task out."""
    return run_report(spec_path, request, config, profile).results


def run_report(spec_path: Path, request: DocumentRequest, config: RuntimeConfig, profile: str | None = None) -> RunReport:
    """The same run as `run_extraction`, returned whole: results + what the run cost (the stable output, `run --out`)."""
    started = time.time()
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
    return run_pipeline_report(spec, manifest, adapters, config, indexed, store=store, profile=profile,
                               started=started, phase_seconds={"index": round(time.time() - started, 2)})


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
            document_names={d.document_id: d.filename for d in manifest.documents},
            schema_hash=spec.schema_hash, runtime_fingerprint=config.fingerprint(),
            models=adapters.model_labels(), stage_versions=STAGE_VERSIONS,
            remote=[f"{cap}:{host}" for cap, host in adapters.remote().items()], engine_version=__version__))


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
    return run_pipeline_report(spec, manifest, adapters, config, indexed, store=store, profile=profile,
                               evidence_out=evidence_out, proposals_out=proposals_out).results


def run_pipeline_report(
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
    started: float | None = None,
    phase_seconds: dict[str, float] | None = None,
) -> RunReport:
    """`run_pipeline`, returned as a RunReport (results + RunSummary: calls, tokens, seconds, recovery)."""
    started = started or time.time()
    for t in spec.tasks:  # where the live graph starts: every task of the spec, before any stage touches it
        trace(t.id, "declared", "done", f"{t.id}: {t.kind.value} task declared", kind=t.kind.value, after=",".join(t.depends_on))
    ctx = _context(spec, manifest, adapters, config, indexed, store, profile)
    ctx.phase_seconds.update(phase_seconds or {})

    # One execution scope for the whole run: the model-call cache (resume) and the meter (what the run cost).
    with execution_scope(store, config.execution) as exe:
        ctx.exe = exe

        # S06 for every task first (no model involved), then S05 looks at all the evidence at once.
        with _timed(ctx, "retrieve"):
            retrieve_all(spec, ctx)
        limit = parallel_limit(config.execution, *adapters.chat_models())
        with _timed(ctx, "extract"):
            ask_all(spec, ctx, limit)

        # Then the tasks, one dependency layer at a time; the tasks of a layer do not depend on each other.
        results: dict[str, TaskResult] = {}
        done, total, lock = [0], len(spec.graph.order), threading.Lock()

        def one(task_id: str) -> TaskResult:
            emit("run", f"task {task_id} ({spec.task(task_id).kind.value})")
            result = process_task(spec.task(task_id), ctx)
            ctx.statuses[task_id] = result.status
            with lock:
                done[0] += 1
                progress("run", done[0], total, "tasks done")
            level = {"supported": "ok", "not_found": "info", "uncertain": "warn", "failed": "error"}[result.status.value]
            emit("S13", f"{task_id}: {result.status.value}", level)
            trace(task_id, "result", "done", outcome=result.status.value, level=level, attempts=sum(a.action != "none" for a in result.recovery))
            return result

        with _timed(ctx, "tasks"):
            if limit > 1:
                emit("run", f"up to {limit} task(s) of a dependency layer run side by side")
            for layer in spec.graph.layers:
                results.update(zip(layer, run_parallel(layer, one, limit)))

    if evidence_out is not None:
        evidence_out.update(ctx.evidence)
    if proposals_out is not None:
        proposals_out.update(ctx.proposals)
    ordered = [results[t.id] for t in spec.tasks]
    summary = summarize(ordered, ctx, time.time() - started, limit)
    emit("run", f"run summary: {summary.total.calls} model call(s) ({summary.total.cached} from the cache, {summary.total.failed} failed), "
                f"{summary.total.prompt_tokens + summary.total.completion_tokens} token(s), {summary.total.seconds:.1f}s of model time; "
                f"{summary.recovery_attempts} recovery attempt(s), {summary.recovered} task(s) improved", "ok")
    report = RunReport(specification=spec.name, results=ordered, summary=summary, execution=ctx.execution,
                       run_id=run_id_of(ctx.execution, ctx.profile), created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    if store is not None:  # S13: every run is kept, so it can be listed, reopened, reviewed and reported later
        store.put(RUNS, report.run_id, report)
        emit("S13", f"run {report.run_id} saved ({len(ordered)} task(s)); reviews are kept apart from it", "ok")
    return report


RUNS = "runs"  # artifact-store kind of saved RunReports


def run_id_of(execution: ExecutionMetadata, profile: str) -> str:
    """Names a run by what determines it: the documents, the specification, the settings, the stage versions, the depth.
    Running the same thing again gives the same id, so a review made on a run still applies to its reproduction."""
    return fingerprint(sorted(execution.document_hashes.items()), execution.schema_hash, execution.runtime_fingerprint,
                       execution.stage_versions, profile)[:16]



def summarize(results: list[TaskResult], ctx: RunContext, seconds: float, limit: int) -> RunSummary:
    """What the run produced and what it cost (from the run meter; no result depends on it)."""
    statuses: dict[str, int] = {}
    for r in results:
        statuses[r.status.value] = statuses.get(r.status.value, 0) + 1
    meter = ctx.exe.meter
    return RunSummary(
        seconds=round(seconds, 2), phase_seconds=ctx.phase_seconds, statuses=statuses, total=meter.total(), stages=meter.stages(),
        tasks=meter.tasks(), recovery_attempts=sum(a.action != "none" for r in results for a in r.recovery),
        recovered=sum(any(a.kept for a in r.recovery) for r in results), concurrency=limit)


class _timed:
    """`with _timed(ctx, "retrieve"):` adds the wall-clock seconds of the block to the run's phase times."""

    def __init__(self, ctx: RunContext, name: str):
        self.ctx, self.name = ctx, name

    def __enter__(self):
        self.started = time.time()

    def __exit__(self, *exc):
        self.ctx.phase_seconds[self.name] = round(self.ctx.phase_seconds.get(self.name, 0) + time.time() - self.started, 2)


def retrieve_all(spec: CompiledSpecification, ctx: RunContext) -> None:
    """S06 for every value / interpretation task, then S05: the plan made from all the evidence."""
    if ctx.tools is None or ctx.indexed is None:
        return  # no index: the retrieval stage is skipped
    retrievable = [t for t in spec.graph.order if spec.task(t).kind != TaskKind.RECORDS]  # records: whole scope, S09
    for n, task_id in enumerate(retrievable, start=1):
        trace(task_id, "retrieve", "start")
        ctx.evidence[task_id] = retrieve_task(spec.task(task_id), ctx, ctx.profile)
        trace(task_id, "retrieve", "done", passages=len(ctx.evidence[task_id].passages))
        progress("S06", n, len(retrievable), "tasks have their evidence")
    values = {t: ctx.evidence[t] for t in retrievable if spec.task(t).kind == TaskKind.VALUE}
    if values:
        ctx.plan = plan_extraction(values, {t: spec.task(t) for t in values}, ctx.config.planning,
                                   ctx.config.extraction_for(ctx.profile), ctx.adapters.extractor)


def retrieve_task(task: TaskSpecification, ctx: RunContext, profile: str, budget_factor: float = 1.0) -> EvidencePackage:
    """S06 for one task at a given depth (recovery calls it again with another profile or a bigger evidence budget)."""
    document_ids = resolve_task_documents(task, ctx.manifest)
    documents = [ctx.indexed[d] for d in document_ids if d in ctx.indexed]
    settings = ctx.retrieval if profile == ctx.profile else ctx.config.retrieval.resolve(profile)
    if budget_factor != 1.0:
        settings = settings.model_copy(update={"budget_tokens": int(settings.budget_tokens * budget_factor)})
    return gather_evidence(request_from_task(task, document_ids), documents, ctx.tools, settings,
                           manifest=ctx.manifest, ledger=ctx.ledger, profile=profile)


def ask_all(spec: CompiledSpecification, ctx: RunContext, limit: int = 1) -> None:
    """S07 steps 1-4: one model call per batch of the plan (a batch is one task, or several that share evidence).

    A task that waits for another task (`depends_on`) is not asked here: whether it is asked at all depends on that task's result."""
    if ctx.plan is None or ctx.indexed is None:
        return
    waits = ctx.config.execution.skip_blocked_dependents
    batches = [b for b in ctx.plan.batches if not (waits and any(spec.task(t).depends_on for t in b.task_ids))]
    done, lock = [0], threading.Lock()

    def ask(batch) -> dict[str, Asked]:
        tasks = [spec.task(t) for t in batch.task_ids]
        document_ids = list(dict.fromkeys(d for t in tasks for d in resolve_task_documents(t, ctx.manifest)))
        for t in batch.task_ids:
            trace(t, "read", "start", kind="value", batch=len(batch.task_ids))
        with task_scope("+".join(batch.task_ids)):
            asked = ask_batch(tasks, ctx.evidence, [ctx.indexed[d] for d in document_ids if d in ctx.indexed], ctx.adapters.extractor,
                              ctx.config.extraction_for(ctx.profile), ctx.config.planning, ledger=ctx.ledger)
        for t in batch.task_ids:
            trace(t, "read", "done", kind="value")
        with lock:
            done[0] += 1
            progress("S07", done[0], len(batches), "extraction calls done")
        return asked

    for asked in run_parallel(batches, ask, min(limit, ctx.adapters.extractor.max_parallel)):
        ctx.asked.update(asked)


def process_task(task: TaskSpecification, ctx: RunContext) -> TaskResult:
    """One task through the stages (its evidence was gathered by `retrieve_all`)."""
    with task_scope(task.id):  # every model call made for this task is counted for it
        return _process_task(task, ctx)


def _process_task(task: TaskSpecification, ctx: RunContext) -> TaskResult:
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

    # S05. Scheduling: a task that waits for a task that was not accepted is not sent to the model (it could not be accepted anyway).
    blocked = [d for d in task.depends_on if d not in ctx.accepted]
    if blocked and ctx.config.execution.skip_blocked_dependents:
        return _blocked_result(task, ctx, blocked, notes)

    # S06. Evidence (gathered in `retrieve_all`; here only its summary). Records are discovered in S09, not by retrieval.
    if task.id in ctx.evidence:
        package = ctx.evidence[task.id]
        notes.append(f"evidence ({ctx.profile}): {len(package.passages)} passage(s), "
                     f"{package.trace.used_tokens}/{package.trace.budget_tokens} tokens, {len(package.gaps)} gap(s)")

    # S09. Records: the whole scope is read (no retrieval), then S10/S11 decide the collection, then S12.
    if task.kind == TaskKind.RECORDS and ctx.indexed is not None:
        return _process_records(task, ctx, document_ids, notes)

    if task.id not in ctx.evidence or task.kind not in (TaskKind.VALUE, TaskKind.INTERPRETATION):
        notes.append("records need indexed documents (S04)" if task.kind == TaskKind.RECORDS
                     else "no evidence was gathered, so nothing could be extracted")
        return TaskResult(task_id=task.id, kind=task.kind, status=TaskStatus.FAILED,
                          completeness=Completeness.UNKNOWN if task.kind == TaskKind.RECORDS else None,
                          diagnostics=notes, execution=ctx.execution)

    # S07 / S08 + S10 + S11. Extract a value (or decide a label), validate it, apply the acceptance policy.
    attempt = attempt_task(task, ctx, document_ids, ctx.evidence[task.id], ctx.profile, asked=ctx.asked.get(task.id))
    if task.id in ctx.asked and ctx.asked[task.id].shared_with:
        notes.append(f"extraction call shared with {', '.join(ctx.asked[task.id].shared_with)}")

    # S12. Recover: bounded, targeted second tries for a task that was not accepted. Each one extracts AND validates again.
    trail: list[RecoveryAttempt] = []
    if _may_recover(ctx) and attempt.status not in DECIDED:
        tools = RecoveryTools(
            retrieve=lambda profile, factor: retrieve_task(task, ctx, profile, factor),
            attempt=lambda package, profile, changes, escalate: attempt_task(task, ctx, document_ids, package, profile, changes=changes, escalate=escalate),
            spent=lambda: ctx.exe.meter.task(task.id), has_escalation=ctx.adapters.escalation_model is not None)
        attempt, trail, recovery_notes = recover_task(task, attempt, tools, ctx.config.recovery, ctx.config.execution)
        notes += recovery_notes

    # S13. The result: what the attempt that stands says, plus the trail of every other try.
    proposal, validated = attempt.proposal, attempt.validated
    ctx.proposals[task.id], ctx.decisions[task.id], ctx.evidence[task.id] = proposal, validated, attempt.package
    status, value, alternatives, evidence = _result_parts(proposal, validated)
    alternatives = _with_other_attempts(alternatives, value, trail)
    if status == TaskStatus.SUPPORTED:
        ctx.accepted.add(task.id)
    notes.append(f"{'extraction' if task.kind == TaskKind.VALUE else 'interpretation'} ({proposal.outcome.value}): {proposal.reason}")
    notes += [f"{status.value}: {why}" for why in validated.decision.reasons[:3]]
    notes += [f"info: {f.message}" for f in validated.report.findings if f.severity.value == "info"][:5]
    notes += [f"recovery: {a.action} -> {a.status_after.value}, {'kept' if a.kept else 'not kept'} ({a.why})" for a in trail]
    return TaskResult(
        task_id=task.id, kind=task.kind, status=status, value=value, alternatives=alternatives, evidence=evidence,
        interpretation=proposal.interpretation, validation=validated.report, acceptance=validated.decision,
        recovery=trail, diagnostics=notes, execution=ctx.execution)


def attempt_task(
    task: TaskSpecification, ctx: RunContext, document_ids: list[str], package: EvidencePackage, profile: str, *,
    changes: dict | None = None, escalate: bool = False, asked: Asked | None = None,
) -> Attempt:
    """ONE try of a value / interpretation task: S07 or S08, then S10 + S11. The first try and every recovery try go through here.

    `changes` are setting changes for this try only ({"extraction": {...}, "interpretation": {...}}); `escalate` asks the
    escalation model instead of the usual one; `asked` is the model's answer when `ask_all` already has it."""
    scope_docs = [ctx.indexed[d] for d in document_ids if d in ctx.indexed]
    changes = changes or {}
    extraction = ctx.config.extraction_for(profile).model_copy(update=changes.get("extraction", {}))
    interpretation = ctx.config.interpretation_for(profile).model_copy(update=changes.get("interpretation", {}))
    stronger = ctx.adapters.escalation_model if escalate else None

    # S07 / S08. Extract a value (value tasks) or decide a label under criteria (interpretation tasks).
    if task.kind == TaskKind.VALUE:
        model = stronger or ctx.adapters.extractor
        if asked is None:  # a first try was asked in `ask_all`; a recovery try asks again
            trace(task.id, "read", "start", kind="value")
        asked = asked or ask_task(task, package, scope_docs, model, extraction, ledger=ctx.ledger)
        proposal = conclude_task(asked, model, extraction)
    else:
        trace(task.id, "read", "start", kind="interpretation")
        proposal = interpret_task(task, package, scope_docs, stronger or ctx.adapters.interpretation_model, interpretation, ledger=ctx.ledger)
    trace(task.id, "read", "done", kind=task.kind.value, found=proposal.outcome.value)

    # S10 + S11. Validate the proposal and apply the engine's acceptance policy.
    retrieval = ctx.retrieval if profile == ctx.profile else ctx.config.retrieval.resolve(profile)
    query = build_queries(request_from_task(task, document_ids), retrieval).rerank
    qualified = is_qualified(ctx.store, qualification_key(ctx.config, ctx.adapters, profile)) \
        if ctx.config.acceptance.require_qualification and not escalate else None
    validated = validate_and_accept(
        task, proposal, package, scope_docs, ctx.config.acceptance,
        scorer=(lambda q, texts: ctx.tools.reranker.score(q, texts)) if ctx.tools else None, query=query,
        verifier=ctx.adapters.verifier, accepted_tasks=ctx.accepted, qualified=qualified,
        retries=extraction.retries, ledger=ctx.ledger)
    return Attempt(proposal=proposal, validated=validated, package=package, profile=profile)


def _may_recover(ctx: RunContext) -> bool:
    return ctx.config.recovery.enabled and ctx.config.execution.max_recovery_attempts > 0 and ctx.exe is not None


def _blocked_result(task: TaskSpecification, ctx: RunContext, blocked: list[str], notes: list[str]) -> TaskResult:
    """A task whose dependency was not accepted: no model call is spent on it, and it inherits the unresolved state.

    A dependency that FAILED (a technical problem) makes this task `failed` too; one that is `uncertain` or `not_found` makes it
    `uncertain`. Never `not_found`: nothing was inspected for this task."""
    status = TaskStatus.FAILED if any(ctx.statuses.get(d) == TaskStatus.FAILED for d in blocked) else TaskStatus.UNCERTAIN
    why = f"task(s) {', '.join(blocked)}, which this task depends on, were not accepted: this task was not sent to the model"
    emit("S05", f"{task.id}: skipped ({why})", "warn")
    report = classify([finding(ValidationLayer.CONSISTENCY, Category.DEPENDENCY_NOT_ACCEPTED, why, task.id)], ctx.config.acceptance)
    decision = AcceptanceDecision(status=status, policy=ctx.config.acceptance.profile, reasons=[why], blocking=report.material)
    return TaskResult(task_id=task.id, kind=task.kind, status=status, validation=report, acceptance=decision,
                      completeness=Completeness.UNKNOWN if task.kind == TaskKind.RECORDS else None,
                      diagnostics=[*notes, f"{status.value}: {why}"], execution=ctx.execution)


def _with_other_attempts(alternatives: list, value, trail: list[RecoveryAttempt]) -> list:
    """Values that recovery attempts proposed and the engine did not keep are shown to the reviewer as alternatives."""
    seen = {json.dumps(v, sort_keys=True, default=str) for v in [*alternatives, value] if v is not None}
    out = list(alternatives)
    for a in trail:
        key = json.dumps(a.value_after, sort_keys=True, default=str)
        if not a.kept and a.value_after is not None and key not in seen:
            seen.add(key)
            out.append(a.value_after)
    return out


def _process_records(task: TaskSpecification, ctx: RunContext, document_ids: list[str], notes: list[str]) -> TaskResult:
    """S09 + S10/S11 + S12 for one records task."""
    docs = [ctx.indexed[d] for d in document_ids if d in ctx.indexed]
    scorer = (lambda q, texts: ctx.tools.reranker.score(q, texts)) if ctx.tools else None

    def decide(outcome) -> RecordsAttempt:
        trace(task.id, "validate", "start", kind="records")
        attempt = RecordsAttempt(outcome, *decide_records(task, outcome, ctx.config.acceptance))
        trace(task.id, "decide", "done", outcome=attempt.status.value, kind="records")
        return attempt

    # S09, then S10 + S11 for the collection.
    trace(task.id, "read", "start", kind="records")
    read = extract_records(task, docs, ctx.adapters, ctx.config, ctx.profile, scorer=scorer, ledger=ctx.ledger)
    trace(task.id, "read", "done", kind="records")
    attempt = decide(read)

    # S12. Recover: read again only what the audit says may be missing; the audit and the policy run again on the result.
    trail: list[RecoveryAttempt] = []
    if _may_recover(ctx) and attempt.status not in DECIDED:
        verified = ctx.config.model_copy(update={"records": ctx.config.records.model_copy(update={"verify_fields": True})})

        def refield(read, record):
            passage = as_passage(record.span, read.document.document_id, read.tagged)
            return extract_fields(task, record.span.record_id, passage, as_evidence(passage), read.document, ctx.adapters, verified, ctx.profile, scorer, ctx.ledger)

        tools = RecordsTools(
            decide=decide,
            reread=lambda read, discovery: read_document(task, read.document, read.tagged, read.scope, discovery, ctx.adapters, ctx.config,
                                                         ctx.profile, scorer=scorer, ledger=ctx.ledger, previous=read.records),
            discover=lambda tagged, scope: discover(task, tagged, scope, ctx.adapters.extractor, ctx.config.records),
            refield=refield, spent=lambda: ctx.exe.meter.task(task.id))
        attempt, trail, recovery_notes = recover_records(task, attempt, tools, ctx.config.recovery, ctx.config.execution)
        notes += recovery_notes

    # S13. The result.
    outcome, decision = attempt.outcome, attempt.decision
    if decision.status == TaskStatus.SUPPORTED:
        ctx.accepted.add(task.id)
    notes += [*outcome.log, f"{decision.status.value}: {decision.reasons[0] if decision.reasons else ''}"]
    notes += [f"recovery: {a.action} -> {a.status_after.value}, {'kept' if a.kept else 'not kept'} ({a.why})" for a in trail]
    evidence = [EvidenceLink(ref=SourceRef(document_id=read.document.document_id, block_id=r.record.block_ids[0]), role=EvidenceRole.SUPPORTS)
                for read in outcome.documents for r in read.records if r.record.block_ids]
    return TaskResult(
        task_id=task.id, kind=task.kind, status=decision.status, records=[r.record for r in outcome.records], completeness=attempt.completeness,
        evidence=evidence, validation=attempt.report, acceptance=decision, recovery=trail, diagnostics=notes,
        scope_note=" | ".join(outcome.scopes), execution=ctx.execution)


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


def result_schema() -> dict:
    """The JSON Schema of the engine's output file (`RunReport`): the contract a consumer of `run --out` can rely on."""
    return RunReport.model_json_schema()
