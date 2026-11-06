"""Runtime configuration: WHICH models and limits to use.

Kept strictly separate from the extraction YAML (which says WHAT to extract).
Everything here can change without touching a task's meaning.
"""

from pathlib import Path

import yaml
from pydantic import Field

from ..artifacts.fingerprint import fingerprint
from ..contracts.common import StrictModel


class AdapterSelection(StrictModel):
    name: str = "fake"  # key in the adapter registry
    params: dict = {}  # passed to the adapter factory (model path, device, ...)


class ModelsConfig(StrictModel):
    parsing: AdapterSelection = AdapterSelection()
    embedding: AdapterSelection = AdapterSelection()
    reranking: AdapterSelection = AdapterSelection()
    extraction: AdapterSelection = AdapterSelection()
    interpretation: AdapterSelection = AdapterSelection()
    verification: AdapterSelection = AdapterSelection()


class ExecutionConfig(StrictModel):
    max_concurrency: int = Field(default=1, ge=1)
    max_recovery_attempts: int = Field(default=2, ge=0)
    max_seconds_per_task: float | None = None


class StorageConfig(StrictModel):
    artifact_dir: Path = Path(".extractor_artifacts")


class ParsingConfig(StrictModel):
    """Stage S03 tuning. Thresholds are meant to be adjusted against your scans."""

    # --- routing: a page goes to OCR when ANY of these holds -----------------
    min_native_chars: int = Field(default=30, ge=0)  # fewer characters => no usable text layer
    max_garbage_ratio: float = Field(default=0.03, ge=0, le=1)  # U+FFFD / private-use / control
    scan_image_coverage: float = Field(default=0.6, ge=0, le=1)  # page mostly one big image...
    scan_max_chars: int = Field(default=150, ge=0)  # ...with little text on top of it
    # --- region fallback inside a native page --------------------------------
    region_min_image_area: float = Field(default=0.05, ge=0, le=1)  # image share of page worth OCR-ing
    region_text_overlap: float = Field(default=0.3, ge=0, le=1)  # image already covered by text above this
    # --- issue detection -----------------------------------------------------
    max_order_jump_ratio: float = Field(default=0.3, ge=0, le=1)  # share of blocks out of reading order
    # --- OCR retry ladder ----------------------------------------------------
    ocr_dpi: int = Field(default=200, ge=72)
    ocr_hires_dpi: int = Field(default=300, ge=72)
    # --- pipeline ------------------------------------------------------------
    use_cache: bool = True


class RuntimeConfig(StrictModel):
    models: ModelsConfig = ModelsConfig()
    execution: ExecutionConfig = ExecutionConfig()
    parsing: ParsingConfig = ParsingConfig()
    storage: StorageConfig = StorageConfig()

    def fingerprint(self) -> str:
        """Identity of the settings that can change results (storage path excluded)."""
        return fingerprint(self.models, self.execution, self.parsing)

    def parsing_fingerprint(self) -> str:
        """Cache key part for parsed documents: only parser choice + parsing settings."""
        return fingerprint(self.models.parsing, self.parsing)


def load_runtime_config(path: Path | None) -> RuntimeConfig:
    """Load a runtime YAML, or return the all-fake defaults when no file is given."""
    if path is None:
        return RuntimeConfig()
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return RuntimeConfig.model_validate(data)
