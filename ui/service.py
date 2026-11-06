"""UI backend logic, independent of the web framework.

Everything here calls the engine through its public modules (`extractor.*`);
the engine knows nothing about this folder. The workspace is a plain folder:

    <workspace>/uploads/    documents you uploaded
    <workspace>/specs/      extraction YAMLs you saved
    <workspace>/reports/    parse outputs (HTML report + JSON)
    <workspace>/runtime.yaml  settings edited in the Settings tab
"""

import re
import threading
import time
from pathlib import Path

import yaml
from pydantic import ValidationError

from extractor.adapters import build_adapters
from extractor.artifacts import ArtifactStore
from extractor.config import RuntimeConfig
from extractor.contracts.specification import SpecificationFile
from extractor.evaluation import CorpusError, load_corpus
from extractor.evaluation.parsing_eval import evaluate_parsing
from extractor.parsing.backends.pdfium_renderer import PdfiumRenderer
from extractor.parsing.report import write_report
from extractor.pipeline import run_pipeline
from extractor.scope import DocumentRequest, ManifestError, build_manifest
from extractor.spec import SpecificationError, compile_specification, parse_specification

PARSEABLE = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}
EXAMPLE_SPEC = Path(__file__).parent.parent / "examples" / "spec.example.yaml"
CORPUS_DIR = Path(__file__).parent.parent / "evaluation" / "corpus"


class UiError(ValueError):
    """A problem to show the user (bad name, missing file...)."""


def _safe_name(name: str) -> str:
    base = Path(name).name
    if not re.fullmatch(r"[\w .()\-]+", base) or base.startswith("."):
        raise UiError(f"unsupported file name: {name!r}")
    return base


class Workspace:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.uploads = self.root / "uploads"
        self.specs = self.root / "specs"
        self.reports = self.root / "reports"
        for d in (self.uploads, self.specs, self.reports):
            d.mkdir(parents=True, exist_ok=True)
        self.config_path = self.root / "runtime.yaml"
        self.store = ArtifactStore(self.root.parent)  # shares models/caches with the CLI
        self._lock = threading.Lock()  # one parse at a time: the GPU is shared
        if not (self.specs / "example.yaml").exists() and EXAMPLE_SPEC.exists():
            (self.specs / "example.yaml").write_text(EXAMPLE_SPEC.read_text(encoding="utf-8"), encoding="utf-8")

    # --- settings ---------------------------------------------------------------

    def default_config(self) -> RuntimeConfig:
        """The UI is for real use, so parsing defaults to the real `layered` parser."""
        base = RuntimeConfig()
        models = base.models.model_copy(
            update={"parsing": base.models.parsing.model_copy(update={"name": "layered"})})
        return base.model_copy(update={"models": models})

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
                })
        return out

    def parse(self, name: str) -> dict:
        """Parse one uploaded document; writes the HTML report and JSON, returns a summary."""
        path = self.document_path(name)
        if path.suffix.lower() not in PARSEABLE:
            raise UiError(f"the parser supports PDF and image files, not '{path.suffix}'")
        config = self.get_config()
        started = time.time()
        with self._lock:
            entry = build_manifest(DocumentRequest.from_paths([path]), self.store).documents[0]
            parser = build_adapters(config, self.store).parser
            rep = parser.parse(entry, path)
            write_report(rep, path, PdfiumRenderer(), self.reports / f"{path.stem}.report.html")
            (self.reports / f"{path.stem}.parsed.json").write_text(rep.model_dump_json(indent=2), encoding="utf-8")
        return {
            "name": name,
            "seconds": round(time.time() - started, 1),
            "pages": [{"page": p.page, "route": p.route, "reason": p.route_reason} for p in rep.pages],
            "blocks": len(rep.blocks),
            "tables": len(rep.tables),
            "issues": [
                {"kind": i.kind.value, "page": i.page, "message": i.message,
                 "attempts": i.attempts, "resolved": i.resolved}
                for i in rep.issues
            ],
        }

    def report_path(self, name: str, kind: str) -> Path:
        stem = Path(_safe_name(name)).stem
        path = self.reports / f"{stem}.{'report.html' if kind == 'report' else 'parsed.json'}"
        if not path.is_file():
            raise UiError(f"'{name}' has not been parsed yet")
        return path

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

    # --- run --------------------------------------------------------------------

    def run(self, spec_name: str, documents: list[str]) -> dict:
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
        results = run_pipeline(spec, manifest, build_adapters(config, self.store), config)
        return {
            "manifest": manifest.model_dump(mode="json"),
            "results": [r.model_dump(mode="json") for r in results],
        }

    # --- evaluation corpus --------------------------------------------------------

    def corpus_status(self) -> dict:
        try:
            corpus = load_corpus(CORPUS_DIR)
        except CorpusError as exc:
            return {"ok": False, "problems": str(exc).splitlines()}
        return {"ok": True, "cases": [c.case_id for c in corpus.cases], "path": str(CORPUS_DIR)}

    def corpus_parsing(self) -> dict:
        corpus = load_corpus(CORPUS_DIR)
        if not corpus.cases:
            raise UiError("the corpus has no cases yet; add some under evaluation/corpus/")
        config = self.get_config()
        with self._lock:
            parser = build_adapters(config, self.store).parser
            metrics, checks = evaluate_parsing(corpus, parser)
        return {
            "metrics": metrics.model_dump(mode="json"),
            "quote_recall": metrics.quote_recall,
            "page_accuracy": metrics.page_accuracy,
            "misses": [c.model_dump(mode="json") for c in checks if not c.found or c.on_expected_page is False],
        }
