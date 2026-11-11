"""UI backend logic, independent of the web framework.

Everything here calls the engine through its public modules (`extractor.*`);
the engine knows nothing about this folder. The workspace is a plain folder:

    <workspace>/uploads/    documents you uploaded
    <workspace>/specs/      extraction YAMLs you saved
    <workspace>/reports/    parse outputs (HTML report + JSON)
    <workspace>/runtime.yaml  settings edited in the Settings tab
"""

import functools
from dataclasses import dataclass
from contextvars import ContextVar
import inspect
import json
import re
import threading
import time
from collections import Counter
from datetime import datetime
from contextlib import contextmanager
from pathlib import Path

import yaml
from pydantic import ValidationError

from extractor.adapters import Capability, build_adapter, build_adapters, registered_adapters
from extractor.authoring import draft_specification
from extractor.artifacts import ArtifactStore, fingerprint
from extractor.config import RuntimeConfig
from extractor.config.runtime import RetrievalSettings
from extractor.contracts.common import TaskKind
from extractor.contracts.task import TaskSpecification
from extractor.extraction import extract_value
from extractor.retrieval.queries import build_queries
from extractor.validation import validate_and_accept
from extractor.events import LOG, emit, run_scope
from extractor.contracts.specification import SpecificationFile
from extractor.contracts.task import LabelSpec
from extractor.evaluation import CorpusError, load_corpus
from extractor.evaluation.acceptance_eval import evaluate_acceptance
from extractor.evaluation.attribution import FIXABLE, attribute
from extractor.evaluation.records_eval import attribute_records, evaluate_records
from extractor.evaluation.extraction_eval import IMPLEMENTED_KINDS, evaluate_extraction
from extractor.interpretation.interpretation_pipeline import interpret_task
from extractor.evaluation.indexing_eval import evaluate_indexing
from extractor.evaluation.overlap_eval import evaluate_overlap
from extractor.evaluation.parsing_eval import evaluate_parsing
from extractor.indexing.builder import IndexBuilder
from extractor.indexing.report import write_index_report
from extractor.indexing.stores import registered_stores
from extractor.parsing.backends.pdfium_renderer import PdfiumRenderer
from extractor.parsing.report import write_report
from extractor.pipeline import index_documents, plan_only, run_id_for, run_pipeline_report
from extractor.evaluation.recovery_eval import evaluate_recovery
from extractor.execution import execution_scope
from extractor.api import Extractor, UnknownRun
from extractor.review import ReviewStore, UnknownTarget
from extractor.config.runtime import RECOVERY_ACTIONS
from extractor.retrieval.retrieval_pipeline import gather_evidence
from extractor.retrieval.types import RetrievalRequest, RetrievalTools, request_from_field, request_from_task
from extractor.scope import DocumentRequest, ManifestError, build_manifest
from extractor.spec import SpecificationError, compile_specification, compile_specification_file, parse_specification

from history import RunHistory

PARSEABLE = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}
EXAMPLE_SPEC = Path(__file__).parent.parent / "examples" / "spec.example.yaml"
CORPUS_DIR = Path(__file__).parent.parent / "evaluation" / "corpus"


class UiError(ValueError):
    """A problem to show the user (bad name, missing file...)."""


_SECRET = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|bearer\s+\S+|(?:api[_-]?key|token|secret|password)\s*[=:]\s*\S+)", re.I)


_IDENT = re.compile(r"[\w .:+,/()-]*")


def public_event(event) -> dict:
    """An engine event safe to show or print: no secrets, no long text (which could be document content),
    and only plain numbers/flags from its data. Used by the live stream and the terminal echo."""
    message = _SECRET.sub("[hidden]", event.message)
    if len(message) > 300:
        message = message[:300] + "..."
    data = {k: v for k, v in event.data.items() if isinstance(v, (int, float, bool))}
    if event.kind == "trace":  # short identifiers that place a task in the pipeline (task id, phase, layer, action, outcome)
        data.update({k: v for k, v in event.data.items() if isinstance(v, str) and len(v) <= 80 and _IDENT.fullmatch(v)})
    return {"seq": event.seq, "time": event.time, "level": event.level, "stage": event.stage,
            "message": message, "run": event.run, "kind": event.kind, "data": data}


def _safe_name(name: str) -> str:
    base = Path(name).name
    if not re.fullmatch(r"[\w .()\-]+", base) or base.startswith("."):
        raise UiError(f"unsupported file name: {name!r}")
    return base


@dataclass
class Pending:
    """A run that is about to start or in progress: its history entry is created when its job STARTS (not when it ends)."""

    kind: str
    params: dict
    started: datetime
    id: str | None = None  # the history id, once registered
    engine_run_id: str = ""  # the id the engine will give the run (known up front for a `run`)


_PENDING: ContextVar[Pending | None] = ContextVar("pending_run", default=None)


def recorded(kind: str):
    """Keep the run in the history FROM ITS START: the entry appears as soon as the job starts (status `running`), is completed with
    its result or error when it ends, and `run_id` is added to the result.

    A request that fails before its job started (bad input such as "type a query") is not recorded; one that fails inside
    the job is, with status `error`, so usage and failures can be tracked.
    """

    def decorate(fn):
        signature = inspect.signature(fn)

        @functools.wraps(fn)
        def wrapper(self, *args, **kwargs):
            bound = signature.bind(self, *args, **kwargs)
            bound.apply_defaults()
            pending = Pending(kind, {k: v for k, v in bound.arguments.items() if k != "self"}, datetime.now())
            token = _PENDING.set(pending)
            try:
                result = fn(self, *args, **kwargs)
            except Exception as exc:
                if pending.id:  # the job had started (and is registered): complete its entry
                    self.runs.finish(pending.id, "error", error=f"{type(exc).__name__}: {exc}")
                raise
            finally:
                _PENDING.reset(token)
            if pending.id is None:  # a call that never opened a job: record it whole
                pending.id = self.runs.start(kind, pending.params, self._settings_summary(), pending.started)
            self.runs.finish(pending.id, "ok", result)
            return {**result, "run_id": pending.id}

        return wrapper

    return decorate


class Workspace:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.uploads = self.root / "uploads"
        self.specs = self.root / "specs"
        self.reports = self.root / "reports"
        for d in (self.uploads, self.specs, self.reports):
            d.mkdir(parents=True, exist_ok=True)
        self.runs = RunHistory(self.root / "history")  # every job the UI ran, kept across restarts
        self.runs.import_legacy(self.root / "corpus_history")
        self.runs.mark_interrupted()  # a run that was in progress when the server last stopped is on record as interrupted
        self.config_path = self.root / "runtime.yaml"
        self.store = ArtifactStore(self.root.parent)  # shares models/caches with the CLI
        self._lock = threading.Lock()  # one parse at a time: the GPU is shared
        self._builders: dict[str, tuple] = {}  # config fingerprint -> (IndexBuilder, AdapterSet); keeps models loaded
        if not (self.specs / "example.yaml").exists() and EXAMPLE_SPEC.exists():
            (self.specs / "example.yaml").write_text(EXAMPLE_SPEC.read_text(encoding="utf-8"), encoding="utf-8")

    @contextmanager
    def _job(self, title: str):
        """One logged job at a time (the GPU is shared): a run in the Logs tab, queued if another one is running."""
        with run_scope(title) as run_id:
            queued = self._lock.locked()
            pending = _PENDING.get()
            if pending is not None:  # the run exists from here on: visible in the history, followable by its events
                if pending.id is None:
                    pending.id = self.runs.start(pending.kind, pending.params, self._settings_summary(), pending.started,
                                                 "queued" if queued else "running", run_id, pending.engine_run_id)
                else:
                    self.runs.update(pending.id, events_run=run_id, status="queued" if queued else "running")
            if queued:
                emit("ui", "another job is running; waiting for it to finish", "warn")
            with self._lock:
                if pending is not None and pending.id:
                    self.runs.update(pending.id, status="running")
                # one execution scope per job: finished model calls are read from the call cache, every call is metered
                with execution_scope(self.store, self.get_config().execution):
                    yield run_id

    def logs(self, since: int = 0, run: str | None = None) -> dict:
        """Engine events newer than `since` (the Logs tab polls this)."""
        return {"events": [e.as_dict() for e in LOG.since(since, run)], "last": LOG.last_seq()}

    def clear_logs(self) -> None:
        LOG.clear()

    def _settings_summary(self) -> dict:
        """The models and retrieval profile a run used, for the history."""
        try:
            config = self.get_config()
            models = {k: v["name"] for k, v in config.models.model_dump().items() if isinstance(v, dict) and "name" in v}
            return {**models, "retrieval_profile": config.retrieval.profile}
        except Exception:  # the history must never break a run
            return {}

    # --- history (every parse, index, search, retrieve, extract, run and corpus measure) ---------

    def history(self, kind: str | None = None, group: str | None = None) -> list[dict]:
        return self.runs.list(kind, group)

    def history_run(self, run_id: str) -> dict:
        record = self.runs.get(_safe_name(run_id).removesuffix(".json"))
        if record is None:
            raise UiError(f"no saved run '{run_id}'")
        return record

    def delete_history_run(self, run_id: str) -> None:
        self.runs.delete(_safe_name(run_id).removesuffix(".json"))

    def clear_history(self, kind: str | None = None, group: str | None = None) -> int:
        return self.runs.clear(kind, group)

    # --- settings ---------------------------------------------------------------

    def default_config(self) -> RuntimeConfig:
        """The UI is for real use: real parser and the default embedding model, not the fakes."""
        base = RuntimeConfig()
        models = base.models.model_copy(update={
            "parsing": base.models.parsing.model_copy(update={"name": "layered"}),
            "embedding": base.models.embedding.model_copy(update={"name": "sentence_transformers"}),
            "reranking": base.models.reranking.model_copy(update={"name": "cross_encoder"})})
        return base.model_copy(update={"models": models})

    def options(self) -> dict:
        """Names the UI can offer (everything registered in the engine) and the retrieval profiles."""
        retrieval = self.get_config().retrieval
        return {"extraction": registered_adapters(Capability.EXTRACTION),
                "interpretation": registered_adapters(Capability.INTERPRETATION),
                "verification": registered_adapters(Capability.VERIFICATION),
                "acceptance_profiles": ["strict", "standard", "lenient"],
                "escalation": registered_adapters(Capability.ESCALATION),
                "recovery_actions": list(RECOVERY_ACTIONS),
                "embedding": registered_adapters(Capability.EMBEDDING),
                "reranking": registered_adapters(Capability.RERANKING),
                "stores": registered_stores(),
                "retrieval": {"active": retrieval.profile,
                              "profiles": {n: retrieval.resolve(n).model_dump(mode="json") for n in retrieval.names()}}}

    def _builder(self, config: RuntimeConfig) -> tuple:
        """IndexBuilder + adapters for this config, reused so the embedding model stays loaded."""
        key = fingerprint(config)
        if key not in self._builders:
            self._builders.clear()  # keep one: models are big
            adapters = build_adapters(config, self.store)
            self._builders[key] = (IndexBuilder(config, adapters, self.store), adapters)
        return self._builders[key]

    def get_config(self) -> RuntimeConfig:
        if self.config_path.exists():
            data = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
            return RuntimeConfig.model_validate(data)
        return self.default_config()

    def set_config(self, data: dict) -> RuntimeConfig:
        try:
            config = RuntimeConfig.model_validate(data)
        except ValidationError as exc:
            raise UiError("; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())) from None
        self.config_path.write_text(yaml.safe_dump(config.model_dump(mode="json")), encoding="utf-8")
        return config

    def reset_config(self) -> RuntimeConfig:
        self.config_path.unlink(missing_ok=True)
        return self.default_config()

    # --- documents --------------------------------------------------------------

    def save_upload(self, name: str, data: bytes) -> str:
        name = _safe_name(name)
        (self.uploads / name).write_bytes(data)
        return name

    def document_path(self, name: str) -> Path:
        path = self.uploads / _safe_name(name)
        if not path.is_file():
            raise UiError(f"unknown document: {name}")
        return path

    def delete_document(self, name: str) -> None:
        self.document_path(name).unlink()

    def list_documents(self) -> list[dict]:
        out = []
        for p in sorted(self.uploads.iterdir()):
            if p.is_file():
                out.append({
                    "name": p.name,
                    "size": p.stat().st_size,
                    "parseable": p.suffix.lower() in PARSEABLE,
                    "parsed": (self.reports / f"{p.stem}.report.html").exists(),
                    "indexed": (self.reports / f"{p.stem}.index.html").exists(),
                })
        return out

    @recorded("parse")
    def parse(self, name: str) -> dict:
        """Parse one uploaded document; writes the HTML report and JSON, returns a summary."""
        path = self.document_path(name)
        if path.suffix.lower() not in PARSEABLE:
            raise UiError(f"the parser supports PDF and image files, not '{path.suffix}'")
        config = self.get_config()
        started = time.time()
        with self._job(f"Parse {name}"):
            entry = build_manifest(DocumentRequest.from_paths([path]), self.store).documents[0]
            parser = build_adapters(config, self.store).parser
            rep = parser.parse(entry, path)
            write_report(rep, path, PdfiumRenderer(), self.reports / f"{path.stem}.report.html")
            (self.reports / f"{path.stem}.parsed.json").write_text(rep.model_dump_json(indent=2), encoding="utf-8")
        return {
            "name": name,
            "seconds": round(time.time() - started, 1),
            "pages": [{"page": p.page, "route": p.route, "reason": p.route_reason,
                       "coverage": p.layout_coverage, "recovered": p.recovered_words} for p in rep.pages],
            "recovered_words": sum(p.recovered_words for p in rep.pages),
            "blocks": len(rep.blocks),
            "tables": len(rep.tables),
            "issues": [
                {"kind": i.kind.value, "page": i.page, "message": i.message,
                 "attempts": i.attempts, "resolved": i.resolved}
                for i in rep.issues
            ],
        }

    def blocks(self, name: str) -> dict:
        """Page sizes and located text blocks of a document (PDF points), for drawing evidence on the page.

        Parses the document first when it has not been parsed in this workspace."""
        path = self.document_path(name)
        parsed = self.reports / f"{path.stem}.parsed.json"
        if not parsed.is_file():
            self.parse(name)
        data = json.loads(parsed.read_text(encoding="utf-8"))
        return {
            "pages": [{"page": p["page"], "width": p["width"], "height": p["height"]} for p in data["pages"]],
            "blocks": [{"id": b["block_id"], "page": b["page"], "bbox": b["bbox"], "text": b["text"]}
                       for b in data["blocks"] if b.get("bbox")],
        }

    def report_path(self, name: str, kind: str) -> Path:
        stem = Path(_safe_name(name)).stem
        suffix = {"report": "report.html", "parsed": "parsed.json", "index": "index.html"}[kind]
        path = self.reports / f"{stem}.{suffix}"
        if not path.is_file():
            raise UiError(f"'{name}' has not been {'chunked' if kind == 'index' else 'parsed'} yet")
        return path

    # --- chunking and indexes (S04) -----------------------------------------------

    @recorded("index")
    def index(self, name: str) -> dict:
        """Parse (cached) + chunk + index one document; writes the HTML view, returns a summary."""
        path = self.document_path(name)
        if path.suffix.lower() not in PARSEABLE:
            raise UiError(f"the parser supports PDF and image files, not '{path.suffix}'")
        config = self.get_config()
        started = time.time()
        with self._job(f"Chunk and index {name}"):
            entry = build_manifest(DocumentRequest.from_paths([path]), self.store).documents[0]
            builder, _ = self._builder(config)
            doc = builder.index(entry, path)
            renderer = PdfiumRenderer()
            write_index_report(doc, path, renderer, self.reports / f"{path.stem}.index.html")
            if not (self.reports / f"{path.stem}.report.html").exists():  # keep the parse view in step
                write_report(doc.representation, path, renderer, self.reports / f"{path.stem}.report.html")
                (self.reports / f"{path.stem}.parsed.json").write_text(
                    doc.representation.model_dump_json(indent=2), encoding="utf-8")
        sizes = [p.token_count for p in doc.passages]
        return {
            "name": name,
            "seconds": round(time.time() - started, 1),
            "embedding_model": doc.embedding_model,
            "chunking": config.indexing.chunking.model_dump(mode="json"),
            "stores": {"lexical": config.indexing.lexical_store.name, "vector": config.indexing.vector_store.name},
            "tokens": {"min": min(sizes, default=0), "max": max(sizes, default=0),
                       "mean": round(sum(sizes) / len(sizes)) if sizes else 0},
            "passages": [{"id": p.passage_id, "kind": p.kind.value, "tokens": p.token_count, "pages": p.pages,
                          "heading_path": p.heading_path, "continued": p.continued_across_pages,
                          "note": p.note, "text": p.text} for p in doc.passages],
            "continuations": [c.model_dump(mode="json") for c in doc.continuations],
            "references": [r.model_dump(mode="json") for r in doc.references],
            "unresolved_gaps": [e.note for e in doc.coverage.unresolved()],
        }

    @recorded("search")
    def search(self, name: str, query: str, k: int = 5) -> dict:
        """Run a query against the document's lexical and vector indexes."""
        if not query.strip():
            raise UiError("type a query")
        path = self.document_path(name)
        config = self.get_config()
        with self._job(f"Search {name}"):
            entry = build_manifest(DocumentRequest.from_paths([path]), self.store).documents[0]
            builder, adapters = self._builder(config)
            doc = builder.index(entry, path)  # cached after the first time
            if not doc.passages:
                raise UiError("this document produced no passages to search")
            lexical = builder.lexical.search(doc.index_key, query, k)
            vector = builder.vectors.query(doc.index_key, adapters.embedder.embed_query(query), k)

        def rows(hits):
            return [{"id": h.id, "score": round(h.score, 4), "heading_path": doc.passage(h.id).heading_path,
                     "pages": doc.passage(h.id).pages, "text": doc.passage(h.id).text} for h in hits]
        return {"query": query, "lexical": rows(lexical), "vector": rows(vector)}

    # --- evidence retrieval (S06) -----------------------------------------------------

    def retrieval_tasks(self, spec_name: str) -> list[dict]:
        """Tasks (and record fields) of a spec that retrieval can serve, as ready-to-use queries."""
        checked = self.validate_spec(self.get_spec(spec_name))
        if not checked["ok"]:
            raise UiError("the spec has errors; fix them in the Specification tab first")
        spec = compile_specification(parse_specification(yaml.safe_load(self.get_spec(spec_name))))
        items = []
        for task in spec.tasks:
            if task.kind == TaskKind.RECORDS:  # records are found over the whole scope; their FIELDS use retrieval
                requests = [(f"{task.id} \u203a {f.id}", request_from_field(f, task), f) for f in task.record.fields]
            else:
                requests = [(task.id, request_from_task(task), task)]
            for label, r, source in requests:
                items.append({"id": r.task_id, "label": label, "kind": r.kind.value, "text": r.description,
                              "entity": r.entity, "time": r.time,
                              "labels": [l.model_dump(mode="json") for l in r.labels],
                              "output": source.output.model_dump(mode="json"), "rules": source.rules.model_dump(mode="json")})
        return items

    def _retrieval_settings(self, config: RuntimeConfig, profile: str | None, overrides: dict | None) -> RetrievalSettings:
        try:
            base = config.retrieval.resolve(profile or None)
            return RetrievalSettings.model_validate({**base.model_dump(), **(overrides or {})})
        except ValidationError as exc:
            raise UiError("; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())) from None
        except ValueError as exc:
            raise UiError(str(exc)) from None

    @recorded("retrieve")
    def retrieve(self, documents: list[str], text: str, entity: str | None = None, time_scope: str | None = None,
                 labels: list[dict] | None = None, profile: str | None = None, overrides: dict | None = None) -> dict:
        """Run evidence retrieval for a free-text request and return every step for display."""
        if not text.strip():
            raise UiError("type what you want to find")
        if not documents:
            raise UiError("select at least one document")
        paths = [self.document_path(d) for d in documents]
        config = self.get_config()
        settings = self._retrieval_settings(config, profile, overrides)
        profile_name = profile or config.retrieval.profile
        try:
            allowed = [LabelSpec(name=l["name"], meaning=l.get("meaning") or l["name"]) for l in labels or [] if l.get("name")]
            manifest = build_manifest(DocumentRequest.from_paths(paths), self.store)
        except (ManifestError, ValidationError) as exc:
            raise UiError(str(exc)) from None
        by_name = {e.filename: e for e in manifest.documents}

        started = time.time()
        with self._job(f"Retrieve evidence from {len(documents)} document(s)"):
            builder, adapters = self._builder(config)
            indexed = [builder.index(by_name[p.name], p) for p in paths]  # parse + chunk + embed, cached
            tools = RetrievalTools(lexical=builder.lexical, vectors=builder.vectors,
                                   embedder=adapters.embedder, reranker=adapters.reranker)
            request = RetrievalRequest(
                task_id="query", kind=TaskKind.INTERPRETATION if allowed else TaskKind.VALUE, description=text.strip(),
                entity=(entity or "").strip() or None, time=(time_scope or "").strip() or None, labels=allowed)
            package = gather_evidence(request, indexed, tools, settings, manifest=manifest, profile=profile_name)
        return self._evidence_view(package, indexed, {e.document_id: e.filename for e in manifest.documents},
                                   settings, round(time.time() - started, 1))

    @staticmethod
    def _evidence_view(package, indexed, filenames: dict, settings: RetrievalSettings, seconds: float) -> dict:
        """Plain-JSON view of an EvidencePackage: each step of retrieval, ready to render."""
        trace = package.trace
        passages = {p.passage_id: p for d in indexed for p in d.passages}
        found_by: dict[str, list[str]] = {}
        for name, ids in trace.candidate_lists.items():
            for rank, pid in enumerate(ids, start=1):
                found_by.setdefault(pid, []).append(f"{name}:{rank}")

        def view(pid: str) -> dict:
            p = passages[pid]
            return {"id": pid, "short": pid.rsplit(":", 1)[-1], "document": filenames.get(p.document_id, p.document_id),
                    "pages": p.pages, "heading_path": p.heading_path, "tokens": p.token_count, "text": p.text,
                    "kind": p.kind.value}

        in_package = {p.passage_id: p.origin.value for p in package.passages}
        everywhere = ["all (short document)"] if trace.short_document_mode else []
        return {
            "profile": trace.profile, "seconds": seconds, "settings": settings.model_dump(mode="json"),
            "short_document_mode": trace.short_document_mode,
            "budget": {"used": trace.used_tokens, "limit": trace.budget_tokens},
            "passages_in_scope": len(passages),
            "queries": trace.queries,
            "candidate_lists": [{"name": n, "rows": [{**view(pid), "rank": i} for i, pid in enumerate(ids, start=1)]}
                                for n, ids in trace.candidate_lists.items()],
            "fused_count": len(trace.fused),
            "ranking": [{**view(pid), "rank": i, "score": score, "selected": pid in trace.selected,
                         "in_package": pid in in_package, "package_origin": in_package.get(pid),
                         "reason": trace.not_selected.get(pid), "found_by": found_by.get(pid, everywhere)}
                        for i, (pid, score) in enumerate(trace.reranked, start=1)],
            "package": [{**view(p.passage_id), "origin": p.origin.value, "role": p.role.value,
                         "provenance": p.provenance, "score": p.rerank_score} for p in package.passages],
            "expansions": [{**view(x.passage_id), "origin": x.origin.value, "from": x.from_passage_id.rsplit(":", 1)[-1],
                            "detail": x.detail, "score": x.rerank_score, "promoted": x.promoted, "included": x.included}
                           for x in trace.expansions],
            "gaps": package.gaps,
        }

    # --- value extraction (S07) -------------------------------------------------------

    @staticmethod
    def _task_from_form(form: dict) -> TaskSpecification:
        """A value or interpretation task described in the UI form (the same fields as the YAML)."""
        labels = [{"name": l["name"], "meaning": l.get("meaning") or l["name"]} for l in form.get("labels") or [] if l.get("name")]
        output = {"type": "label" if labels else form.get("type") or "string",  # allowed answers make it a label task
                  "cardinality": form.get("cardinality") or "one", "labels": labels}
        rules = {k: form[k].strip() for k in ("normalization", "derivation", "ambiguity") if (form.get(k) or "").strip()}
        rules["precedence"] = [l.strip() for l in (form.get("precedence") or "").split("\n") if l.strip()]
        rules["on_absent"] = form.get("on_absent") or "not_found"
        rules["decision_criteria"] = [c.strip() for c in (form.get("criteria") or "").splitlines() if c.strip()]
        interpretation = form.get("kind") == "interpretation"
        if interpretation and not labels:
            raise UiError("an interpretation task needs the allowed answers (labels)")
        try:
            return TaskSpecification(
                id="query", kind=TaskKind.INTERPRETATION if interpretation else TaskKind.VALUE, description=(form.get("text") or "").strip(), output=output,
                scope={"entity": (form.get("entity") or "").strip() or None, "time": (form.get("time") or "").strip() or None},
                rules=rules)
        except ValidationError as exc:
            raise UiError("; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())) from None

    @recorded("extract")
    def extract(self, documents: list[str], form: dict, profile: str | None = None, overrides: dict | None = None) -> dict:
        """Retrieve evidence for a value task, run S07 on it and return every step for display."""
        if not (form.get("text") or "").strip():
            raise UiError("type the question to answer")
        if not documents:
            raise UiError("select at least one document")
        task = self._task_from_form(form)
        paths = [self.document_path(d) for d in documents]
        config = self.get_config()
        settings = self._retrieval_settings(config, profile, overrides)
        profile_name = profile or config.retrieval.profile
        try:
            manifest = build_manifest(DocumentRequest.from_paths(paths), self.store)
        except ManifestError as exc:
            raise UiError(str(exc)) from None
        by_name = {e.filename: e for e in manifest.documents}

        started = time.time()
        with self._job(f"Extract a value from {len(documents)} document(s)"):
            builder, adapters = self._builder(config)
            indexed = [builder.index(by_name[p.name], p) for p in paths]
            tools = RetrievalTools(lexical=builder.lexical, vectors=builder.vectors,
                                   embedder=adapters.embedder, reranker=adapters.reranker)
            package = gather_evidence(request_from_task(task), indexed, tools, settings, manifest=manifest, profile=profile_name)
            retrieved_at = time.time()
            if task.kind == TaskKind.INTERPRETATION:                            # S08
                proposal = interpret_task(task, package, indexed, adapters.interpretation_model, config.interpretation_for(profile_name))
            else:                                                               # S07
                proposal = extract_value(task, package, indexed, adapters.extractor, config.extraction_for(profile_name))
            validated = validate_and_accept(                                    # S10 + S11
                task, proposal, package, indexed, config.acceptance, scorer=lambda q, t: adapters.reranker.score(q, t),
                query=build_queries(request_from_task(task), settings).rerank, verifier=adapters.verifier,
                retries=config.extraction_for(profile_name).retries)
        filenames = {e.document_id: e.filename for e in manifest.documents}
        passages = {p.passage_id: p for d in indexed for p in d.passages}
        evidence = self._evidence_view(package, indexed, filenames, settings, round(retrieved_at - started, 1))
        chosen = {c.candidate_id for c in proposal.chosen}

        def candidate(c) -> dict:
            p = passages.get(c.passage_id or "")
            return {**c.model_dump(mode="json"), "chosen": c.candidate_id in chosen,
                    "short": c.passage_id.rsplit(":", 1)[-1] if c.passage_id else None,
                    "document": filenames.get(p.document_id) if p else None, "pages": p.pages if p else []}

        return {
            "task": task.model_dump(mode="json"),
            "model": (adapters.interpretation_model if task.kind == TaskKind.INTERPRETATION else adapters.extractor).identity.label(),
            "interpretation": proposal.interpretation.model_dump(mode="json") if proposal.interpretation else None,
            "outcome": proposal.outcome.value, "value": proposal.value, "reason": proposal.reason, "steps": proposal.steps,
            "gaps": proposal.gaps, "candidates": [candidate(c) for c in proposal.candidates],
            "calls": [{"purpose": c.purpose, "system": c.prompt.system, "user": c.prompt.user, "schema": c.prompt.json_schema,
                       "raw": c.reply.raw, "error": c.reply.error, "seconds": c.reply.seconds,
                       "prompt_tokens": c.reply.prompt_tokens, "completion_tokens": c.reply.completion_tokens}
                      for c in [*proposal.calls, *validated.calls]],
            "evidence": evidence, "seconds": round(time.time() - started, 1),
            "would_be": validated.decision.status.value,
            "validation": {
                "status": validated.decision.status.value, "policy": validated.decision.policy, "reasons": validated.decision.reasons,
                "findings": [f.model_dump(mode="json") for f in validated.report.findings],
                "hints": [h.model_dump(mode="json") for h in validated.decision.hints]},
        }

    # --- specifications ---------------------------------------------------------

    def list_specs(self) -> list[str]:
        return sorted(p.name for p in self.specs.glob("*.yaml"))

    def get_spec(self, name: str) -> str:
        path = self.specs / _safe_name(name)
        if not path.is_file():
            raise UiError(f"unknown spec: {name}")
        return path.read_text(encoding="utf-8")

    def save_spec(self, name: str, text: str) -> str:
        name = _safe_name(name if name.endswith(".yaml") else f"{name}.yaml")
        (self.specs / name).write_text(text, encoding="utf-8")
        return name

    @staticmethod
    def validate_spec(text: str) -> dict:
        """Compile spec text. Never raises: returns ok/errors/tasks/layers/review."""
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            return {"ok": False, "errors": [{"code": "yaml_syntax", "path": "", "message": str(exc)}]}
        try:
            spec = compile_specification(parse_specification(data))
        except SpecificationError as exc:
            return {"ok": False, "errors": [i.model_dump(mode="json") for i in exc.issues]}
        return {
            "ok": True,
            "name": spec.name,
            "tasks": [{"id": t.id, "kind": t.kind.value, "description": t.description} for t in spec.tasks],
            "layers": spec.graph.layers,
            "review": [i.model_dump(mode="json") for i in spec.review_issues],
        }

    def draft_spec(self, request: str, current_yaml: str | None = None) -> dict:
        """S02 authoring: a specification written from a plain-language request (one model call, compiled before it is returned).

        Loads only the chat model (never the parser or embedder). `current_yaml` makes it an edit of that spec."""
        config = self.get_config()
        if config.models.extraction.name == "fake":
            raise UiError("no chat model is configured: choose one for 'extraction' in Settings first")
        try:
            model = build_adapter(Capability.EXTRACTION, config, self.store)
        except ValueError as exc:
            raise UiError(str(exc)) from None
        with self._job("Draft specification"):
            try:
                return draft_specification(request, model, current_yaml).as_dict()
            except ValueError as exc:
                raise UiError(str(exc)) from None

    # --- run --------------------------------------------------------------------

    def _prepare_run(self, spec_name: str, documents: list[str]) -> tuple:
        """What a run needs before its job starts: the checks that can reject the request (nothing is recorded if one does)."""
        if not documents:
            raise UiError("select at least one document")
        paths = [self.document_path(d) for d in documents]
        checked = self.validate_spec(self.get_spec(spec_name))
        if not checked["ok"]:
            raise UiError("the spec has errors; fix them in the Specification tab first")
        spec = compile_specification(parse_specification(yaml.safe_load(self.get_spec(spec_name))))
        config = self.get_config()
        try:
            manifest = build_manifest(DocumentRequest.from_paths(paths), self.store)
        except ManifestError as exc:
            raise UiError(str(exc)) from None
        return spec, manifest, config

    @recorded("run")
    def run(self, spec_name: str, documents: list[str]) -> dict:
        """The whole run, waiting for it to end (the History entry exists from its start all the same)."""
        pending = _PENDING.get()
        prepared = self._prepare_run(spec_name, documents)
        if pending is not None:
            pending.engine_run_id = run_id_for(prepared[0], prepared[1], prepared[2])
        return self._execute_run(spec_name, documents, prepared)

    def start_run(self, spec_name: str, documents: list[str]) -> dict:
        """Start a run in the background and return AT ONCE with its id. The run is on record (queued / running) from this moment: it can be
        followed by polling `/api/history/<id>` and its events, found again after a page refresh or from another tab, and its result appears in
        the same entry when it ends. The shared GPU still runs one job at a time (a second run waits, `queued`)."""
        prepared = self._prepare_run(spec_name, documents)  # raises UiError before anything is recorded
        spec, manifest, config = prepared
        pending = Pending("run", {"spec_name": spec_name, "documents": documents}, datetime.now(), engine_run_id=run_id_for(spec, manifest, config))
        pending.id = self.runs.start(pending.kind, pending.params, self._settings_summary(), pending.started,
                                     "queued" if self._lock.locked() else "running", "", pending.engine_run_id)

        def work() -> None:
            token = _PENDING.set(pending)
            try:
                result = self._execute_run(spec_name, documents, prepared)
            except BaseException as exc:  # noqa: BLE001 - a background thread must record its failure, not die silently
                self.runs.finish(pending.id, "error", error=f"{type(exc).__name__}: {exc}")
            else:
                self.runs.finish(pending.id, "ok", result)
            finally:
                _PENDING.reset(token)

        threading.Thread(target=work, name=f"run-{pending.id}", daemon=True).start()
        return {"run_id": pending.id, "status": "running", "engine_run_id": pending.engine_run_id}

    def _execute_run(self, spec_name: str, documents: list[str], prepared: tuple) -> dict:
        spec, manifest, config = prepared
        with self._job(f"Run {spec_name} on {len(documents)} document(s)") as run_id:
            adapters = build_adapters(config, self.store)
            indexed = index_documents(manifest, adapters, config, self.store)  # S03 + S04 (cached)
            report = run_pipeline_report(spec, manifest, adapters, config, indexed, store=self.store)  # S05-S13, recovery included
        # What the history keeps next to the answer: the spec as it was (the name may be reused later) and where every field went
        # (sanitized `trace` events), so a past run can be reopened and its pipeline replayed.
        trace = [public_event(e) for e in LOG.since(0, run_id) if e.kind in ("trace", "run_start")]
        return {
            "manifest": manifest.model_dump(mode="json"),
            "results": [r.model_dump(mode="json") for r in report.results],
            "summary": report.summary.model_dump(mode="json"),  # calls, tokens, seconds, recovery: what the run cost
            "engine_run_id": report.run_id, "execution": report.execution.model_dump(mode="json"),  # S13: the run is saved under this id; reviews attach to it
            "spec_text": self.get_spec(spec_name),
            "trace": trace,
        }


    # --- saved runs, reviews and reports (S13) --------------------------------------------------------
    # (`self.runs` is the UI job history; these are the ENGINE's runs: results saved under their run id, with reviews kept apart)

    def _api(self) -> Extractor:
        api = Extractor(self.get_config())
        api.store = self.store  # the workspace store: the same place `run` saved the run
        api.reviews = ReviewStore(self.store)
        return api

    def engine_runs(self) -> list[dict]:
        return self._api().runs()

    def _unknown(self, exc: UnknownRun) -> UiError:
        return UiError(str(exc.args[0]))

    def run_detail(self, run_id: str) -> dict:
        try:
            return self._api().run_report(run_id).model_dump(mode="json")
        except UnknownRun as exc:
            raise self._unknown(exc) from None

    def run_reviews(self, run_id: str) -> dict:
        """The reviews of a run and the answers a person ends up with (the engine's, or the reviewer's where there is a review)."""
        api = self._api()
        try:
            return {"reviews": [r.model_dump(mode="json") for r in api.reviews_of(run_id)],
                    "items": [i.model_dump(mode="json") for i in api.reviewed(run_id)]}
        except UnknownRun as exc:
            raise self._unknown(exc) from None

    def add_review(self, run_id: str, task_id: str, decision: str, record: str | None, field: str | None, value, note: str, reviewer: str) -> dict:
        try:
            made = self._api().review(run_id, task_id, decision, record=record or None, field=field or None, value=value, note=note, reviewer=reviewer)
        except UnknownRun as exc:
            raise self._unknown(exc) from None
        except (ValueError, UnknownTarget) as exc:  # nothing to accept, a correction without a value, a task that is not in the run...
            raise UiError(str(exc)) from None
        return made.model_dump(mode="json")

    def run_report_html(self, run_id: str) -> str:
        try:
            return self._api().report_html(run_id)
        except UnknownRun as exc:
            raise self._unknown(exc) from None

    def run_labels(self, run_id: str) -> dict:
        try:
            return self._api().labels(run_id)
        except UnknownRun as exc:
            raise self._unknown(exc) from None

    @recorded("plan")
    def plan(self, spec_name: str, documents: list[str], grouping: str | None = None) -> dict:
        """S06 + S05 for a spec without asking the chat model: the evidence each task gets and how the tasks would be batched.

        `grouping` ("off" / "auto") previews a setting without saving it; empty = the one in Settings."""
        if not documents:
            raise UiError("select at least one document")
        if grouping not in (None, "", "off", "auto"):
            raise UiError("grouping must be off or auto")
        paths = [self.document_path(d) for d in documents]
        checked = self.validate_spec(self.get_spec(spec_name))
        if not checked["ok"]:
            raise UiError("the spec has errors; fix them in the Specification tab first")
        spec = compile_specification(parse_specification(yaml.safe_load(self.get_spec(spec_name))))
        config = self.get_config()
        if grouping:
            config = config.model_copy(update={"planning": config.planning.model_copy(update={"grouping": grouping})})
        try:
            manifest = build_manifest(DocumentRequest.from_paths(paths), self.store)
        except ManifestError as exc:
            raise UiError(str(exc)) from None
        with self._job(f"Plan {spec_name} on {len(documents)} document(s)"):
            adapters = build_adapters(config, self.store)
            indexed = index_documents(manifest, adapters, config, self.store)  # S03 + S04 (cached)
            ctx = plan_only(spec, manifest, adapters, config, indexed, self.store)
        if ctx.plan is None:
            raise UiError("the spec has no value tasks to plan")
        return {"grouping": config.planning.grouping, "plan": ctx.plan.model_dump(mode="json"),
                "tasks": [{"id": t, "description": spec.task(t).description, "passages": len(ctx.evidence[t].passages),
                           "tokens": sum(p.token_count for p in ctx.evidence[t].passages)} for t in ctx.plan.task_order()]}

    # --- evaluation corpus --------------------------------------------------------

    def corpus_status(self) -> dict:
        try:
            corpus = load_corpus(CORPUS_DIR)
        except CorpusError as exc:
            return {"ok": False, "problems": str(exc).splitlines()}
        kinds = Counter(k for c in corpus.cases for k in self._expected_kinds(c))
        return {"ok": True, "cases": [c.case_id for c in corpus.cases],
                "case_info": [{"id": c.case_id, "documents": c.documents, "spec": c.spec, "tags": c.tags} for c in corpus.cases], "path": str(CORPUS_DIR),
                "tags": sorted({t for c in corpus.cases for t in c.tags}),
                "kinds": [{"kind": k.value, "labeled": kinds.get(k, 0), "implemented": k in IMPLEMENTED_KINDS} for k in TaskKind]}

    @staticmethod
    def _expected_kinds(case) -> list[TaskKind]:
        """Kind of every labeled task of a case, read from its spec."""
        spec = compile_specification_file(CORPUS_DIR / case.spec)
        return [t.kind for t in spec.tasks if t.id in case.expected]

    @recorded("corpus.indexing")
    def corpus_indexing(self) -> dict:
        corpus = load_corpus(CORPUS_DIR)
        if not corpus.cases:
            raise UiError("the corpus has no cases yet; add some under evaluation/corpus/")
        config = self.get_config()
        with self._job("Measure chunking quality"):
            builder, _ = self._builder(config)
            metrics = evaluate_indexing(corpus, builder)
        return {"metrics": metrics.model_dump(mode="json"), "containment": metrics.containment}

    @recorded("corpus.overlap")
    def corpus_overlap(self, cases: list[str] | None = None) -> dict:
        """How much evidence the value tasks of each case share (retrieval only; the chat model is not used)."""
        corpus = load_corpus(CORPUS_DIR)
        if not corpus.cases:
            raise UiError("the corpus has no cases yet; add some under evaluation/corpus/")
        config = self.get_config()
        with self._job("Measure evidence overlap"):
            _, adapters = self._builder(config)
            try:
                report = evaluate_overlap(corpus, config, adapters, self.store, [config.retrieval.profile], cases or None)
            except ValueError as exc:
                raise UiError(str(exc))
        return {"rows": [r.model_dump(mode="json") for r in report.rows]}

    @recorded("corpus.extraction")
    def corpus_extraction(self, kinds: list[str] | None = None, tags: list[str] | None = None,
                          cases: list[str] | None = None, groupings: list[str] | None = None) -> dict:
        corpus = load_corpus(CORPUS_DIR)
        if not corpus.cases:
            raise UiError("the corpus has no cases yet; add some under evaluation/corpus/")
        config = self.get_config()
        with self._job("Measure value extraction"):
            _, adapters = self._builder(config)
            saved = self.store.root / "evals" / time.strftime("extraction-%Y%m%d-%H%M%S")  # kept: `eval --attribute <folder>` reads it
            try:
                report = evaluate_extraction(corpus, config, adapters, self.store, [config.retrieval.profile],
                                             [TaskKind(k) for k in kinds or []] or None, tags or None, cases or None, groupings or None,
                                             save_dir=saved)
            except ValueError as exc:
                raise UiError(str(exc))
        causes = attribute(saved)  # which stage caused each miss, and whether it can be fixed
        return {"rows": [r.model_dump(mode="json") for r in report.rows],
                "misses": [o.model_dump(mode="json") for o in report.outcomes
                           if o.outcome not in ("correct", "correct_absence", "correct_uncertain")],
                "differences": report.differences(), "saved_to": str(saved),
                "causes": {"counts": causes.counts(), "fixable": FIXABLE, "rows": [r.model_dump(mode="json") for r in causes.rows if r.cause != "ok"]}}

    @recorded("corpus.acceptance")
    def corpus_acceptance(self, kinds: list[str] | None = None, tags: list[str] | None = None,
                          cases: list[str] | None = None, qualify: bool = False) -> dict:
        corpus = load_corpus(CORPUS_DIR)
        if not corpus.cases:
            raise UiError("the corpus has no cases yet; add some under evaluation/corpus/")
        config = self.get_config()
        with self._job("Measure acceptance"):
            _, adapters = self._builder(config)
            try:
                report = evaluate_acceptance(corpus, config, adapters, self.store, [config.retrieval.profile],
                                             [TaskKind(k) for k in kinds or []] or None, tags or None, cases or None, qualify=qualify)
            except ValueError as exc:
                raise UiError(str(exc))
        rows = [{**r.model_dump(mode="json"), "automation_rate": r.automation_rate, "accepted_precision": r.accepted_precision,
                 "false_accept_rate": r.false_accept_rate, "false_absence_rate": r.false_absence_rate} for r in report.rows]
        return {"rows": rows, "by_split": _acceptance_by_split(report.outcomes),
                "misses": [o.model_dump(mode="json") for o in report.outcomes if o.outcome not in ("accepted_correct", "correct_absence", "correct_uncertain")]}

    @recorded("corpus.records")
    def corpus_records(self, tags: list[str] | None = None, cases: list[str] | None = None, tasks: list[str] | None = None) -> dict:
        """S09 measure: record recall / precision, field accuracy, completeness, and why each miss happened."""
        corpus = load_corpus(CORPUS_DIR)
        if not corpus.cases:
            raise UiError("the corpus has no cases yet; add some under evaluation/corpus/")
        config = self.get_config()
        with self._job("Measure records extraction"):
            _, adapters = self._builder(config)
            saved = self.store.root / "evals" / time.strftime("records-%Y%m%d-%H%M%S")
            try:
                report = evaluate_records(corpus, config, adapters, self.store, [config.retrieval.profile], tags or None, cases or None,
                                          tasks or None, save_dir=saved)
            except ValueError as exc:
                raise UiError(str(exc))
        return {"scores": [{**s.model_dump(mode="json"), "recall": s.recall, "precision": s.precision} for s in report.scores],
                "totals": report.totals(), "saved_to": str(saved),
                "by_split": {split: report.totals(split) for split in sorted({s.split for s in report.scores})},
                "causes": [c.model_dump(mode="json") for c in attribute_records(saved)]}

    @recorded("corpus.recovery")
    def corpus_recovery(self, kinds: list[str] | None = None, tags: list[str] | None = None, cases: list[str] | None = None) -> dict:
        """S12 measure: the whole engine on the labeled cases; what recovery rescued, what it harmed and what each rescue cost."""
        corpus = load_corpus(CORPUS_DIR)
        if not corpus.cases:
            raise UiError("the corpus has no cases yet; add some under evaluation/corpus/")
        config = self.get_config()
        with self._job("Measure recovery"):
            _, adapters = self._builder(config)
            saved = self.store.root / "evals" / time.strftime("recovery-%Y%m%d-%H%M%S")
            try:
                report = evaluate_recovery(corpus, config, adapters, self.store, None, [TaskKind(k) for k in kinds or []] or None,
                                           tags or None, cases or None, save_dir=saved)
            except ValueError as exc:
                raise UiError(str(exc))
        return {**report.model_dump(mode="json"), "saved_to": str(saved), "enabled": config.recovery.enabled,
                "allowed": list(config.recovery.actions)}

    @recorded("corpus.parsing")
    def corpus_parsing(self) -> dict:
        corpus = load_corpus(CORPUS_DIR)
        if not corpus.cases:
            raise UiError("the corpus has no cases yet; add some under evaluation/corpus/")
        config = self.get_config()
        with self._job("Measure parsing quality"):
            parser = build_adapters(config, self.store).parser
            metrics, checks = evaluate_parsing(corpus, parser)
        return {
            "metrics": metrics.model_dump(mode="json"),
            "quote_recall": metrics.quote_recall,
            "page_accuracy": metrics.page_accuracy,
            "misses": [c.model_dump(mode="json") for c in checks if not c.found or c.on_expected_page is False],
        }


def _acceptance_by_split(outcomes) -> list[dict]:
    """The acceptance KPIs per `split:` tag of the cases (train / validation / test): the test row is the one to report."""
    rows = []
    for split in sorted({o.split for o in outcomes}):
        mine = [o.outcome for o in outcomes if o.split == split]
        accepted = mine.count("accepted_correct") + mine.count("false_accept")
        rows.append({"split": split, "tasks": len(mine), "automation_rate": accepted / len(mine) if mine else None,
                     "accepted_precision": mine.count("accepted_correct") / accepted if accepted else None,
                     "false_accepts": mine.count("false_accept"), "false_absences": mine.count("false_absence"),
                     "over_abstentions": mine.count("over_abstention")})
    return rows
