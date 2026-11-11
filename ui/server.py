"""Web server: thin HTTP layer over `service.Workspace`. Routes only, no logic."""

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from extractor.events import LOG
from service import UiError, Workspace, public_event

STATIC = Path(__file__).parent / "static"


class SpecBody(BaseModel):
    text: str
    name: str | None = None


class DraftBody(BaseModel):
    request: str  # what the user wants to extract, in their own words
    current: str | None = None  # the YAML being edited: the request is then a change to it


class SearchBody(BaseModel):
    query: str


class RunBody(BaseModel):
    spec: str
    documents: list[str]


class ReviewBody(BaseModel):
    task_id: str
    decision: str  # accept | correct | reject
    record: str | None = None  # a records task: the record's key
    field: str | None = None  # ...and one of its fields
    value: object = None  # the right value (decision = correct)
    note: str = ""
    reviewer: str = ""


class RetrieveBody(BaseModel):
    documents: list[str]
    text: str
    entity: str | None = None
    time: str | None = None
    labels: list[dict] = []  # [{"name": ..., "meaning": ...}]: makes it an interpretation-style request
    profile: str | None = None
    overrides: dict = {}  # RetrievalSettings fields to change for this query only


class CorpusExtractBody(BaseModel):
    kinds: list[str] = []  # task kinds to score (empty = every implemented kind)
    tags: list[str] = []  # only cases having all these tags (e.g. split:test)
    cases: list[str] = []  # only these case ids (empty = all)
    qualify: bool = False  # acceptance only: write a qualification record when the thresholds are met
    groupings: list[str] = []  # extraction only: S05 grouping settings to compare (e.g. ["off", "auto"]; empty = the one in Settings)


class CorpusRecordsBody(BaseModel):
    tags: list[str] = []
    cases: list[str] = []
    tasks: list[str] = []  # only these records tasks (empty = all)


class PlanBody(BaseModel):
    spec: str
    documents: list[str]
    grouping: str | None = None  # preview "off" or "auto" without saving it (empty = the Settings value)


class ExtractBody(BaseModel):
    documents: list[str]
    task: dict  # the form: text, type, cardinality, labels, entity, time, precedence, derivation, normalization, ambiguity, on_absent
    profile: str | None = None
    overrides: dict = {}


def create_app(workspace_dir: Path) -> FastAPI:
    ws = Workspace(workspace_dir)
    app = FastAPI(title="Extractor UI")

    @app.exception_handler(UiError)
    async def ui_error(_, exc: UiError):
        return JSONResponse({"error": str(exc)}, status_code=400)

    @app.get("/", response_class=HTMLResponse)
    def index():
        return (STATIC / "index.html").read_text(encoding="utf-8")

    # --- logs ---
    @app.get("/api/logs")
    def logs(since: int = 0, run: str | None = None):  # events newer than `since`; polled by the Logs tab
        return ws.logs(since, run)

    @app.get("/api/logs/stream")
    async def logs_stream(request: Request, since: int | None = None, run: str | None = None):
        """Server-sent events: engine events as they happen (sanitized). `since` defaults to "from now"."""

        async def gen():
            seq = LOG.last_seq() if since is None else since
            idle = 0
            while not await request.is_disconnected():
                events = LOG.since(seq)
                for e in events:
                    seq = e.seq
                    if run is None or e.run == run:
                        yield f"id: {e.seq}\ndata: {json.dumps(public_event(e))}\n\n"
                idle = 0 if events else idle + 1
                if idle and idle % 60 == 0:
                    yield ": keep-alive\n\n"
                await asyncio.sleep(0.25)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.delete("/api/logs")
    def clear_logs():
        ws.clear_logs()
        return {"ok": True}

    # --- documents ---
    @app.get("/api/documents")
    def documents():
        return ws.list_documents()

    @app.post("/api/documents")
    async def upload(files: list[UploadFile] = File(...)):
        return [ws.save_upload(f.filename or "", await f.read()) for f in files]

    @app.delete("/api/documents/{name}")
    def delete_document(name: str):
        ws.delete_document(name)
        return {"ok": True}

    @app.get("/api/documents/{name}/file")
    def document_file(name: str):  # the original file, for the PDF viewer
        path = ws.document_path(name)
        return FileResponse(path, media_type="application/pdf" if path.suffix.lower() == ".pdf" else None)

    @app.get("/api/documents/{name}/blocks")
    def document_blocks(name: str):  # located text blocks per page (parses first if needed)
        return ws.blocks(name)

    @app.post("/api/documents/{name}/parse")
    def parse(name: str):  # sync endpoint: FastAPI runs it in a worker thread
        return ws.parse(name)

    @app.get("/api/documents/{name}/report")
    def report(name: str):
        return FileResponse(ws.report_path(name, "report"), media_type="text/html")

    @app.post("/api/documents/{name}/index")
    def index_document(name: str):  # chunk + index (S04); loads the embedding model on first use
        return ws.index(name)

    @app.get("/api/documents/{name}/index-report")
    def index_report(name: str):
        return FileResponse(ws.report_path(name, "index"), media_type="text/html")

    @app.post("/api/documents/{name}/search")
    def search(name: str, body: SearchBody):
        return ws.search(name, body.query)

    @app.get("/api/documents/{name}/parsed")
    def parsed(name: str):
        return FileResponse(ws.report_path(name, "parsed"), media_type="application/json")

    # --- specs ---
    @app.get("/api/specs")
    def specs():
        return ws.list_specs()

    @app.get("/api/specs/{name}")
    def get_spec(name: str):
        return {"name": name, "text": ws.get_spec(name)}

    @app.post("/api/specs/validate")
    def validate(body: SpecBody):
        return ws.validate_spec(body.text)

    @app.post("/api/specs/draft")
    def draft_spec(body: DraftBody):  # sync: one chat-model call (plus a repair call at most)
        return ws.draft_spec(body.request, body.current)

    @app.post("/api/specs")
    def save_spec(body: SpecBody):
        return {"name": ws.save_spec(body.name or "untitled", body.text)}

    @app.get("/api/specs/{name}/retrieval-tasks")
    def retrieval_tasks(name: str):  # tasks / record fields of a spec as ready-made retrieval queries
        return ws.retrieval_tasks(name)

    # --- evidence retrieval (S06) ---
    @app.post("/api/retrieve")
    def retrieve(body: RetrieveBody):  # sync: loads models on first use, runs in a worker thread
        return ws.retrieve(body.documents, body.text, body.entity, body.time, body.labels, body.profile, body.overrides)

    # --- value extraction (S07) ---
    @app.post("/api/extract")
    def extract(body: ExtractBody):  # sync: retrieval + one or more model calls
        return ws.extract(body.documents, body.task, body.profile, body.overrides)

    # --- plan (S05) ---
    @app.get("/api/engine-runs")
    def engine_runs():
        return ws.engine_runs()

    @app.get("/api/engine-runs/{run_id}")
    def run_detail(run_id: str):
        return ws.run_detail(run_id)

    @app.get("/api/engine-runs/{run_id}/reviews")
    def run_reviews(run_id: str):
        return ws.run_reviews(run_id)

    @app.post("/api/engine-runs/{run_id}/reviews")
    def add_review(run_id: str, body: ReviewBody):
        return ws.add_review(run_id, body.task_id, body.decision, body.record, body.field, body.value, body.note, body.reviewer)

    @app.get("/api/engine-runs/{run_id}/report", response_class=HTMLResponse)
    def run_report_html(run_id: str):
        return ws.run_report_html(run_id)

    @app.get("/api/engine-runs/{run_id}/labels")
    def run_labels(run_id: str):
        return ws.run_labels(run_id)

    @app.post("/api/plan")
    def plan(body: PlanBody):  # sync: retrieval only, no chat model
        return ws.plan(body.spec, body.documents, body.grouping)

    # --- run ---
    @app.post("/api/run")
    def run(body: RunBody):
        return ws.run(body.spec, body.documents)

    # --- settings ---
    @app.get("/api/options")
    def options():
        return ws.options()

    @app.get("/api/config")
    def get_config():
        return ws.get_config().model_dump(mode="json")

    @app.put("/api/config")
    def set_config(data: dict):
        return ws.set_config(data).model_dump(mode="json")

    @app.delete("/api/config")
    def reset_config():
        return ws.reset_config().model_dump(mode="json")

    # --- history of every run ---
    @app.get("/api/history")
    def history(kind: str | None = None, group: str | None = None):
        return ws.history(kind, group)

    @app.get("/api/history/{run_id}")
    def history_run(run_id: str):
        return ws.history_run(run_id)

    @app.delete("/api/history/{run_id}")
    def delete_history_run(run_id: str):
        ws.delete_history_run(run_id)
        return {"ok": True}

    @app.delete("/api/history")
    def clear_history(kind: str | None = None, group: str | None = None):
        return {"deleted": ws.clear_history(kind, group)}

    # --- corpus ---
    @app.get("/api/corpus")
    def corpus():
        return ws.corpus_status()

    @app.post("/api/corpus/indexing")
    def corpus_indexing():
        return ws.corpus_indexing()

    @app.post("/api/corpus/overlap")
    def corpus_overlap(body: CorpusExtractBody):
        return ws.corpus_overlap(body.cases)

    @app.post("/api/corpus/extraction")
    def corpus_extraction(body: CorpusExtractBody):
        return ws.corpus_extraction(body.kinds, body.tags, body.cases, body.groupings)

    @app.post("/api/corpus/acceptance")
    def corpus_acceptance(body: CorpusExtractBody):
        return ws.corpus_acceptance(body.kinds, body.tags, body.cases, body.qualify)

    @app.post("/api/corpus/records")
    def corpus_records(body: CorpusRecordsBody):
        return ws.corpus_records(body.tags, body.cases, body.tasks)

    @app.post("/api/corpus/recovery")
    def corpus_recovery(body: CorpusExtractBody):
        return ws.corpus_recovery(body.kinds, body.tags, body.cases)

    @app.post("/api/corpus/parsing")
    def corpus_parsing():
        return ws.corpus_parsing()

    return app
