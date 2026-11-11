"""Runtime configuration: WHICH models and limits to use.

Kept strictly separate from the extraction YAML (which says WHAT to extract).
Everything here can change without touching a task's meaning.
"""

from pathlib import Path
from typing import Literal

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
    escalation: AdapterSelection = AdapterSelection()  # a stronger chat model that ONLY recovery (S12) may ask; `fake` = no escalation


class ExecutionConfig(StrictModel):
    """How a run is carried out (never what it decides)."""

    max_concurrency: int = Field(default=1, ge=1)  # tasks of one dependency layer run side by side, capped by the model's own `max_parallel`
    max_recovery_attempts: int = Field(default=2, ge=0)  # S12: recovery tries per task (0 = no recovery)
    max_seconds_per_task: float | None = None  # S12: no new recovery attempt starts once a task has used this much model time (None = only `recovery.max_seconds`)
    cache_model_calls: bool = True  # a call that already gave a usable answer is read from the artifact store, not made again (resume, cheap re-measurement)
    retry_pause_seconds: float = Field(default=2.0, ge=0)  # pause before re-sending a call that failed in transport (times the attempt number)
    skip_blocked_dependents: bool = True  # a task whose `depends_on` was not accepted is not sent to the model (it could not be accepted anyway)


RECOVERY_ACTIONS = ("retry_call", "widen_evidence", "rediscover", "refield", "deepen", "escalate_model")


class RecoverySettings(StrictModel):
    """S12: what the engine may try when a task was not accepted. Every attempt re-runs extraction AND validation.

    `actions` lists the actions that are allowed. The default has only the ones that COMPLETE an inspection the engine knows
    is incomplete (a failed call, evidence that did not fit, numbered text no record covers). `refield`, `deepen` and
    `escalate_model` change how the answer is produced, so they stay off until `eval --recovery` shows on your corpus that
    they rescue answers without creating a false accept."""

    enabled: bool = True
    actions: list[Literal["retry_call", "widen_evidence", "rediscover", "refield", "deepen", "escalate_model"]] = ["retry_call", "widen_evidence", "rediscover"]
    max_calls: int = Field(default=6, ge=0)  # extra model calls recovery may spend on one task (a records task: per round)
    max_seconds: float = Field(default=180.0, ge=0)  # extra model seconds recovery may spend on one task
    evidence_budget_factor: float = Field(default=2.0, gt=1)  # widen_evidence: the task's evidence budget is multiplied by this
    deeper_profile: dict[str, str] = {"light": "standard", "standard": "deep"}  # deepen: the next depth after each profile
    rediscover_margin_blocks: int = Field(default=2, ge=0)  # rediscover: blocks read on each side of an uncovered region


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
    # --- completeness check against the PDF's own text layer ------------------
    reconcile: bool = True  # compare the layout parse with the text layer and restore missing words
    reconcile_min_missing_words: int = Field(default=1, ge=1)  # restore only when at least this many are missing
    reconcile_box_tolerance: float = Field(default=3.0, ge=0)  # points of slack when matching a word to a block
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


class ExtractionSettings(StrictModel):
    """S07 knobs: how the value-extraction step talks to the model (WHICH model is `models.extraction`)."""

    max_candidates: int = Field(default=8, ge=1)  # candidates the model may report per call
    retries: int = Field(default=1, ge=0)  # extra attempts when a call fails or returns unusable JSON
    prompt_margin: float = Field(default=0.1, ge=0.0, lt=0.9)  # share of the context left free (token counts are estimates)
    choose_with_model: bool = True  # let the model pick among conflicting candidates when the task gives precedence rules
    unresolved_references_block_absence: bool = True  # "see Section 9" that could not be found keeps an empty answer `uncertain`
    unresolved_attachments_block_absence: bool = False  # same for "Schedule A" / "Exhibit 3" / Appendix / Annex: attachments missing from the supplied files
    passages_per_call: int | None = Field(default=None, ge=1)  # None = as many passages per call as the context allows; 1 = one call per passage (more focus, more calls)
    think_extraction: bool | None = None  # None = the adapter default; True = reason before answering (slower)
    think_verification: bool | None = None
    think_choice: bool | None = None
    screen_candidates: bool = True  # deterministic gates: redacted, value not in its quote, blank field, unreadable typed value
    verify_candidates: bool = True  # one extra model call removes candidates that do not answer THE FIELD
    allow_unlocated_quotes: bool = True  # keep candidates whose quote is not in the evidence (flagged), instead of dropping them


class PlanningSettings(StrictModel):
    """S05 knobs: which value tasks may share ONE model call. Every limit is a plain number, so a model with a larger
    context or a bigger output allowance only needs different numbers here (nothing in the code names a model)."""

    grouping: Literal["off", "auto"] = "off"  # off = one extraction call per task; auto = tasks that share evidence share a call
    max_fields_per_call: int = Field(default=3, ge=2)  # tasks answered by one call
    max_foreign_share: float = Field(default=0.3, ge=0, le=1)  # a task may see at most this share of passages that its own retrieval did not pick
    output_tokens_per_field: int = Field(default=256, ge=1)  # expected size of one task's answer: fields x this must fit the model's output limit
    reask_absent: Literal["always", "never"] = "always"  # a task the shared call found nothing for is asked again alone (false absence is the worst error)


class InterpretationSettings(StrictModel):
    """S08 knobs: how interpretation tasks (label decisions under criteria) talk to the model.

    WHICH model is `models.interpretation` (when it is left as `fake`, the extraction model is used)."""

    max_evidence: int = Field(default=10, ge=1)  # quotes the model may list per call
    retries: int = Field(default=1, ge=0)
    prompt_margin: float = Field(default=0.1, ge=0.0, lt=0.9)
    think: bool | None = True  # True = reason before answering. On the corpus (30 tasks, Qwen3 8B) it took correct answers from 24 to 27 of 30, at 2x the time
    think_judge: bool | None = True  # None = the adapter default; False = fast
    judge: bool = True  # one extra call that can only downgrade a label (never adds or changes one)
    tools: list[str] = ["calculate", "compare_numbers"]  # deterministic tools the model may ask the engine to run (arithmetic)
    max_tool_calls: int = Field(default=3, ge=0)
    unresolved_references_block_absence: bool = True  # same meaning as in `extraction:`
    unresolved_attachments_block_absence: bool = False


class RecordSettings(StrictModel):
    """S09 knobs: how repeated records are found. The fields of each record use `extraction:` / `interpretation:`."""

    scope_with_model: bool = True  # a model reads the document OUTLINE and names the region; the engine checks it (False = whole document)
    scope_min_windows: int = Field(default=3, ge=1)  # ...but only for documents that need at least this many discovery windows; smaller ones are read whole (a wrong region is the worst error, and a small document is cheap to read)
    scope_margin_blocks: int = Field(default=1, ge=0)  # blocks added on each side of the region the model names
    outline_chars_per_block: int = Field(default=70, ge=20)
    window_overlap_blocks: int = Field(default=2, ge=0)  # blocks repeated at the start of the next discovery window
    max_records_per_window: int = Field(default=60, ge=1)  # upper bound of records per answer (the model's output limit usually allows fewer); a full answer is followed by another call
    retries: int = Field(default=1, ge=0)
    prompt_margin: float = Field(default=0.1, ge=0.0, lt=0.9)
    think_discovery: bool | None = False  # listing boundaries is reading, not deciding: thinking only adds time
    verify_discovery: bool = False  # one call that drops listed entries that do not match the record definition. Off: on the corpus an 8B model rejected 75 entries, real definitions included (recall matters more than this filter)
    verify_fields: bool = False  # S07 candidate verification for the fields of each record (they already read only their own record: off saves 2 of 3 calls)
    check_numbering: bool = True  # (a) (b) (d) -> a record is missing


BUILTIN_INTERPRETATION_PROFILES: dict[str, dict] = {"light": {"think": False, "think_judge": False}, "deep": {"think": True, "think_judge": True}}


class QualificationThresholds(StrictModel):
    """What a model + settings combination must reach on YOUR labeled corpus to be qualified (S11)."""

    min_tasks: int = Field(default=20, ge=1)
    min_accepted_precision: float = Field(default=0.95, ge=0, le=1)  # share of `supported` answers that are right
    max_false_absence_rate: float = Field(default=0.02, ge=0, le=1)  # share of tasks wrongly called not_found


class AcceptanceSettings(StrictModel):
    """S10/S11: which findings block acceptance, and the optional checks. Never a model confidence number."""

    profile: Literal["strict", "standard", "lenient"] = "standard"
    block: list[str] = []  # finding categories that block in addition to the profile
    allow: list[str] = []  # finding categories that never block, whatever the profile
    risk_relevance_ratio: float = Field(default=0.5, ge=0, le=1)  # a damaged region matters when its relevance reaches this share of the best evidence
    semantic_check: bool = False  # S10 semantic support: one call to models.verification on the chosen answer
    think_semantic: bool | None = None
    require_qualification: bool = False  # unqualified model + settings cap the result at `uncertain`
    qualification: QualificationThresholds = QualificationThresholds()


# Extraction depth that goes with each retrieval profile. `deep` lets the model reason (measured: fixes about 40% of the
# errors left at standard depth, at 2-3x the time). Override or add profiles under `extraction_profiles:` in the runtime config.
BUILTIN_EXTRACTION_PROFILES: dict[str, dict] = {
    "deep": {"think_extraction": True, "think_verification": True},
}


class RuntimeConfig(StrictModel):
    models: ModelsConfig = ModelsConfig()
    execution: ExecutionConfig = ExecutionConfig()
    parsing: ParsingConfig = ParsingConfig()
    indexing: IndexingConfig = IndexingConfig()
    retrieval: RetrievalConfig = RetrievalConfig()
    extraction: ExtractionSettings = ExtractionSettings()
    planning: PlanningSettings = PlanningSettings()
    acceptance: AcceptanceSettings = AcceptanceSettings()
    extraction_profiles: dict[str, dict] = {}  # profile name -> ExtractionSettings fields to change at that depth
    interpretation: InterpretationSettings = InterpretationSettings()
    records: RecordSettings = RecordSettings()
    recovery: RecoverySettings = RecoverySettings()
    interpretation_profiles: dict[str, dict] = {}  # profile name -> InterpretationSettings fields to change at that depth
    storage: StorageConfig = StorageConfig()

    def fingerprint(self) -> str:
        """Identity of the settings that can change results (storage path excluded)."""
        return fingerprint(self.models, self.execution, self.parsing, self.indexing.chunking,
                           self.retrieval.resolve(), self.extraction, self.planning, self.extraction_profiles, self.acceptance,
                           self.interpretation, self.interpretation_profiles, self.records, self.recovery)

    def extraction_for(self, profile: str | None = None) -> ExtractionSettings:
        """S07 settings at the given depth: the base `extraction:` block, the built-in profile of that name, then yours."""
        name = profile or self.retrieval.profile
        changes = {**BUILTIN_EXTRACTION_PROFILES.get(name, {}), **self.extraction_profiles.get(name, {})}
        return self.extraction.model_copy(update=changes) if not changes else ExtractionSettings.model_validate(
            {**self.extraction.model_dump(), **changes})

    def interpretation_for(self, profile: str | None = None) -> InterpretationSettings:
        """S08 settings at the given depth: the base `interpretation:` block, the built-in profile of that name, then yours."""
        name = profile or self.retrieval.profile
        changes = {**BUILTIN_INTERPRETATION_PROFILES.get(name, {}), **self.interpretation_profiles.get(name, {})}
        return InterpretationSettings.model_validate({**self.interpretation.model_dump(), **changes})

    @model_validator(mode="after")
    def _validate_extraction_profiles(self):
        for name in self.extraction_profiles:
            self.extraction_for(name)  # fail at config load, not in the middle of a run
        for name in self.interpretation_profiles:
            self.interpretation_for(name)
        return self

    def parsing_fingerprint(self) -> str:
        """Cache key part for parsed documents: only parser choice + parsing settings."""
        return fingerprint(self.models.parsing, self.parsing)


def load_runtime_config(path: Path | None) -> RuntimeConfig:
    """Load a runtime YAML, or return the all-fake defaults when no file is given."""
    if path is None:
        return RuntimeConfig()
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return RuntimeConfig.model_validate(data)
