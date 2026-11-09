"""S11 - Model qualification: has THIS model with THESE settings been measured on your corpus?

Interface compatibility does not establish extraction quality (ARCHITECTURE.md section 9). A
qualification record is written by `eval --acceptance --qualify` when the corpus metrics reach the
thresholds in `acceptance.qualification`. Acceptance can then require it (`acceptance.require_qualification`).
The record is keyed by everything that can change answers: the models, the extraction settings at the
retrieval profile in force, the retrieval settings, and the acceptance settings (not the thresholds).
"""

from datetime import datetime

from ..adapters.registry import AdapterSet
from ..artifacts import ArtifactStore, fingerprint
from ..config import RuntimeConfig

KIND = "qualification"


def qualification_key(config: RuntimeConfig, adapters: AdapterSet, profile: str | None = None) -> str:
    name = profile or config.retrieval.profile
    acceptance = config.acceptance.model_dump(exclude={"require_qualification", "qualification"})
    labels = adapters.model_labels()
    return fingerprint(name, labels.get("extraction"), labels.get("verification"), config.extraction_for(name),
                       config.retrieval.resolve(name), acceptance)


def is_qualified(store: ArtifactStore | None, key: str) -> bool:
    return store is not None and store.has(KIND, key)


def write_qualification(store: ArtifactStore, key: str, metrics: dict, thresholds: dict) -> None:
    store.put(KIND, key, {"qualified_at": datetime.now().isoformat(timespec="seconds"), "metrics": metrics, "thresholds": thresholds})
