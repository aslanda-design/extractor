"""The S03 ParserAdapter: native text first, OCR where the page needs it.

Registered as the `layered` parsing adapter. Choosing it in the runtime config:

    models:
      parsing: {name: layered}

It wires the real backends (OpenDataLoader + Docling + pdfium) lazily, so
importing the package never loads heavy libraries. Tests inject fakes instead.
"""

from pathlib import Path

from ..adapters.base import AdapterCapabilities, AdapterContext, ModelIdentity, ParserAdapter
from ..artifacts.fingerprint import fingerprint
from ..contracts.document import DocumentRepresentation
from ..contracts.manifest import DocumentEntry
from .backends.base import NativeBackend, OcrBackend, PageRenderer, ReferenceTextBackend
from .parsing_pipeline import parse_document


def _version(package: str) -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version(package)
    except PackageNotFoundError:
        return "not-installed"


# Loading Docling's models is slow, so real OCR backends are shared by every
# LayeredParser built with the same settings (e.g. when the UI changes a threshold).
_OCR_BACKENDS: dict[tuple, OcrBackend] = {}


class LayeredParser(ParserAdapter):
    def __init__(
        self,
        context: AdapterContext | None = None,
        *,
        native: NativeBackend | None = None,
        ocr: OcrBackend | None = None,
        renderer: PageRenderer | None = None,
        reference: ReferenceTextBackend | None = None,
        languages: list[str] | None = None,
        device: str = "auto",
    ):
        super().__init__(context)
        self._native, self._ocr, self._renderer = native, ocr, renderer
        self._reference = reference
        self._languages, self._device = languages or ["en"], device
        self._injected = native is not None or ocr is not None or reference is not None

    @property
    def identity(self) -> ModelIdentity:
        if self._injected:
            return ModelIdentity(name="layered(injected)", revision="test")
        revision = f"odl-{_version('opendataloader-pdf')}+docling-{_version('docling')}+reconcile-1"
        return ModelIdentity(
            name="layered", revision=revision,
            settings={"languages": self._languages, "device": self._device},
        )

    @property
    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(modalities=["text", "image"], languages=self._languages)

    # --- lazy real backends --------------------------------------------------

    def _backends(self) -> tuple[NativeBackend, OcrBackend, PageRenderer, ReferenceTextBackend | None]:
        if self._native is None:
            from .backends.opendataloader import OpenDataLoaderBackend

            self._native = OpenDataLoaderBackend()
        if self._ocr is None:
            from .backends.docling_ocr import DoclingOcrBackend

            models_dir = self.context.store.root / "models" if self.context.store else None
            key = (tuple(self._languages), self._device, str(models_dir))
            if key not in _OCR_BACKENDS:
                _OCR_BACKENDS[key] = DoclingOcrBackend(
                    languages=self._languages, device=self._device, models_dir=models_dir)
            self._ocr = _OCR_BACKENDS[key]
        if self._renderer is None:
            from .backends.pdfium_renderer import PdfiumRenderer

            self._renderer = PdfiumRenderer()
        if self._reference is None and not self._injected:  # injected (test) backends opt in explicitly
            from .backends.pdfium_text import PdfiumReferenceText

            self._reference = PdfiumReferenceText()
        return self._native, self._ocr, self._renderer, self._reference

    # --- ParserAdapter -------------------------------------------------------

    def parse(self, entry: DocumentEntry, source_path: Path) -> DocumentRepresentation:
        config, store = self.context.parsing, self.context.store
        key = fingerprint(entry.sha256, config, self.identity)
        if store and config.use_cache:
            cached = store.get_model("parsed", key, DocumentRepresentation)
            if cached is not None:
                return cached

        native, ocr, renderer, reference = self._backends()
        representation, extraction = parse_document(
            entry.document_id, source_path, entry.format, config, native, ocr, renderer, reference
        )
        if store:
            if extraction is not None:
                store.put("raw_parse", key, {"backend": native.name, "raw": extraction.raw})
                representation = representation.model_copy(update={"raw_artifact": key})
            store.put("parsed", key, representation)
        return representation
