"""Web server: thin HTTP layer over `service.Workspace`. Routes only, no logic."""

from pathlib import Path

from fastapi import FastAPI, File, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

from service import UiError, Workspace

STATIC = Path(__file__).parent / "static"


class SpecBody(BaseModel):
    text: str
    name: str | None = None


class SearchBody(BaseModel):
    query: str


class RunBody(BaseModel):
    spec: str
    documents: list[str]


def create_app(workspace_dir: Path) -> FastAPI:
    ws = Workspace(workspace_dir)
    app = FastAPI(title="Extractor UI")

    @app.exception_handler(UiError)
    async def ui_error(_, exc: UiError):
        return JSONResponse({"error": str(exc)}, status_code=400)

    @app.get("/", response_class=HTMLResponse)
    def index():
        return (STATIC / "index.html").read_text(encoding="utf-8")

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

    @app.post("/api/specs")
    def save_spec(body: SpecBody):
        return {"name": ws.save_spec(body.name or "untitled", body.text)}

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

    # --- corpus ---
    @app.get("/api/corpus")
    def corpus():
        return ws.corpus_status()

    @app.post("/api/corpus/indexing")
    def corpus_indexing():
        return ws.corpus_indexing()

    @app.post("/api/corpus/parsing")
    def corpus_parsing():
        return ws.corpus_parsing()

    return app
