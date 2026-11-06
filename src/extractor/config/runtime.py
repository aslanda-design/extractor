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


class RuntimeConfig(StrictModel):
    models: ModelsConfig = ModelsConfig()
    execution: ExecutionConfig = ExecutionConfig()
    storage: StorageConfig = StorageConfig()

    def fingerprint(self) -> str:
        """Identity of the settings that can change results (storage path excluded)."""
        return fingerprint(self.models, self.execution)


def load_runtime_config(path: Path | None) -> RuntimeConfig:
    """Load a runtime YAML, or return the all-fake defaults when no file is given."""
    if path is None:
        return RuntimeConfig()
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return RuntimeConfig.model_validate(data)
