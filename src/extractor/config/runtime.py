"""Runtime configuration: WHICH models and limits to use.

Kept strictly separate from the extraction YAML (which says WHAT to extract).
Everything here can change without touching a task's meaning.
"""

from pathlib import Path

import yaml
from pydantic import Field, model_validator

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


class ChunkingConfig(StrictModel):
    """S04 passage-building rules. Sizes are in tokens of the embedding model's tokenizer."""

    max_tokens: int = Field(default=350, ge=16)  # a passage never exceeds this (except an unsplittable word)
    min_tokens: int = Field(default=40, ge=0)  # smaller section groups merge with a neighbour
    infer_depth_from_numbering: bool = True  # "1.2.3", "Article V" when parser levels are flat
    link_continuations: bool = True  # join paragraphs / tables that span a page break
    include_heading_in_index: bool = True  # prepend the heading path to the text that gets indexed
    table_first_row_is_header: bool = True  # when the parser flags no header cells
    resolve_references: bool = True


class StoreSelection(StrictModel):
    """Which index implementation to use. `name` is a key in the store registry."""

    name: str
    params: dict = {}  # passed to the store factory (path, url, api key env var, ...)


class IndexingConfig(StrictModel):
    chunking: ChunkingConfig = ChunkingConfig()
    lexical_store: StoreSelection = StoreSelection(name="sqlite_fts5")
    vector_store: StoreSelection = StoreSelection(name="numpy")


class RetrievalSettings(StrictModel):
    """S06 knobs for ONE depth of extraction. Pick a whole set by profile name (see RetrievalConfig)."""

    # --- candidate sources (step 2) -------------------------------------------------
    candidates_per_list: int = Field(default=30, ge=1)  # kept from each of lexical / vector / structural
    use_lexical: bool = True
    use_vector: bool = True
    use_structural: bool = True  # heading and table-header matches
    # --- fusion + rerank (steps 3-4) --------------------------------------------------
    rrf_k: int = Field(default=60, ge=1)  # reciprocal rank fusion constant
    rerank: bool = True  # False = keep the fused order
    rerank_top_n: int = Field(default=30, ge=1)  # fused candidates sent to the reranker
    short_doc_passages: int = Field(default=64, ge=0)  # documents this small: rerank ALL passages
    # --- selection (step 5) -------------------------------------------------------------
    max_passages: int = Field(default=8, ge=1)  # primary passages kept
    min_passages: int = Field(default=1, ge=1)
    score_drop_ratio: float = Field(default=0.3, ge=0, le=1)  # drop passages scoring < ratio * best (0 = off)
    dedupe_similarity: float = Field(default=0.85, ge=0, le=1)  # word-shingle overlap that counts as a duplicate
    max_per_section: int = Field(default=3, ge=1)
    # --- qualifiers / contradictions ------------------------------------------------------
    qualifier_pass: bool = True
    qualifier_slots: int = Field(default=2, ge=0)  # extra passages reserved for qualifier candidates
    qualifier_candidates: int = Field(default=10, ge=1)
    qualifier_keywords: list[str] = [
        "except", "unless", "subject to", "notwithstanding", "provided that", "provided, however",
        "amended", "amendment", "supersede", "superseded", "replaced", "in lieu of", "waive", "waived",
        "condition", "conditional", "on the condition",
    ]
    # --- expansion (step 6) ------------------------------------------------------------------
    neighbours: int = Field(default=1, ge=0)  # passages added on each side of a selected one
    expand_references: bool = True  # sections that selected passages point at ("see Section 5.2")
    max_reference_passages: int = Field(default=2, ge=1)
    expand_definitions: bool = True  # definitions of defined terms used by selected passages
    max_expansion_rounds: int = Field(default=1, ge=0)  # promoted passages are expanded again
    promote_ratio: float = Field(default=0.6, ge=0, le=1)  # expansion passage scoring >= ratio * best becomes primary
    # --- packaging (step 7) -----------------------------------------------------------------------
    budget_tokens: int = Field(default=4000, ge=1)  # evidence tokens per task (a real model may lower it)


BUILTIN_PROFILES: dict[str, dict] = {
    # cheap and fast: few candidates, no expansion, no qualifier pass
    "light": {
        "candidates_per_list": 15, "rerank_top_n": 15, "max_passages": 4, "budget_tokens": 2000,
        "neighbours": 0, "expand_references": False, "expand_definitions": False,
        "qualifier_pass": False, "short_doc_passages": 24,
    },
    "standard": {},
    # thorough: wide candidate pools, more evidence, two expansion rounds
    "deep": {
        "candidates_per_list": 60, "rerank_top_n": 60, "max_passages": 15, "budget_tokens": 8000,
        "score_drop_ratio": 0.2, "max_per_section": 5, "qualifier_slots": 3, "qualifier_candidates": 20,
        "neighbours": 2, "max_reference_passages": 3, "max_expansion_rounds": 2, "short_doc_passages": 128,
    },
}


class RetrievalConfig(StrictModel):
    """Which retrieval depth to use, and the named depths available.

        retrieval:
          profile: standard                 # or light / deep / your own
          profiles:
            thorough_legal:                 # a custom profile
              extends: deep                 # start from another profile (default: standard)
              budget_tokens: 12000
            light: {max_passages: 3}        # tweaking a built-in one
    """

    profile: str = "standard"
    profiles: dict[str, dict] = {}

    def names(self) -> list[str]:
        return sorted(set(BUILTIN_PROFILES) | set(self.profiles))

    def resolve(self, name: str | None = None) -> RetrievalSettings:
        return RetrievalSettings.model_validate(self._definition(name or self.profile, ()))

    def _definition(self, name: str, seen: tuple[str, ...]) -> dict:
        if name in seen:
            raise ValueError(f"retrieval profile cycle: {' -> '.join(seen + (name,))}")
        if name not in BUILTIN_PROFILES and name not in self.profiles:
            raise ValueError(f"unknown retrieval profile '{name}' (available: {self.names()})")
        own = {**BUILTIN_PROFILES.get(name, {}), **self.profiles.get(name, {})}
        parent = own.pop("extends", None if name == "standard" else "standard")
        base = self._definition(parent, seen + (name,)) if parent else {}
        return {**base, **own}

    @model_validator(mode="after")
    def _validate_profiles(self):
        for name in {*self.names(), self.profile}:  # fail at config load, not in the middle of a run
            self.resolve(name)
        return self


class RuntimeConfig(StrictModel):
    models: ModelsConfig = ModelsConfig()
    execution: ExecutionConfig = ExecutionConfig()
    parsing: ParsingConfig = ParsingConfig()
    indexing: IndexingConfig = IndexingConfig()
    retrieval: RetrievalConfig = RetrievalConfig()
    storage: StorageConfig = StorageConfig()

    def fingerprint(self) -> str:
        """Identity of the settings that can change results (storage path excluded)."""
        return fingerprint(self.models, self.execution, self.parsing, self.indexing.chunking,
                           self.retrieval.resolve())

    def parsing_fingerprint(self) -> str:
        """Cache key part for parsed documents: only parser choice + parsing settings."""
        return fingerprint(self.models.parsing, self.parsing)


def load_runtime_config(path: Path | None) -> RuntimeConfig:
    """Load a runtime YAML, or return the all-fake defaults when no file is given."""
    if path is None:
        return RuntimeConfig()
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return RuntimeConfig.model_validate(data)
