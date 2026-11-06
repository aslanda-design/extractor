"""EvidencePackage: selected source content for a task (stage S06).

The package holds whole passages (what an extraction step reads) and, for
citation, the source blocks they came from. Roles are PROVISIONAL: retrieval
only knows why a passage was included (`origin`); whether it finally supports,
contradicts or qualifies an answer is decided by extraction.
"""

from enum import StrEnum

from .common import EvidenceRole, MutableModel, SourceRef, StrictModel


class EvidenceOrigin(StrEnum):
    RETRIEVED = "retrieved"  # ranked relevant to the task
    QUALIFIER = "qualifier"  # found by the qualifier pass (exceptions, amendments, conditions)
    NEIGHBOUR = "neighbour"  # adjacent to a selected passage
    REFERENCE = "reference"  # section pointed at by a selected passage
    DEFINITION = "definition"  # defines a term a selected passage uses


class EvidenceItem(StrictModel):
    """One cited source block."""

    ref: SourceRef
    role: EvidenceRole = EvidenceRole.SUPPORTS
    retrieval_provenance: list[str] = []  # e.g. ["lexical:3", "vector:1", "rerank:2"]


class EvidencePassage(StrictModel):
    passage_id: str
    document_id: str
    text: str
    heading_path: list[str] = []
    pages: list[int] = []
    block_ids: list[str] = []
    token_count: int
    origin: EvidenceOrigin
    role: EvidenceRole  # provisional, derived from origin
    provenance: list[str] = []  # why it is here: "lexical:3", "vector:1", "all", "neighbour of ...", ...
    rerank_score: float | None = None


class ExpansionRecord(StrictModel):
    passage_id: str
    origin: EvidenceOrigin
    from_passage_id: str  # the selected passage that triggered it
    detail: str = ""  # e.g. "Section 5.2", "Maturity Date"
    rerank_score: float | None = None
    promoted: bool = False  # scored high enough to count as primary evidence
    included: bool = True  # False if it did not fit the budget


class RetrievalTrace(MutableModel):
    """Everything needed to see WHY the package looks like it does (and to measure each step)."""

    profile: str = ""
    queries: dict[str, list[str]] = {}
    short_document_mode: bool = False
    candidate_lists: dict[str, list[str]] = {}  # source -> passage ids in rank order
    fused: list[str] = []
    reranked: list[tuple[str, float]] = []
    selected: list[str] = []  # primary passages after selection
    not_selected: dict[str, str] = {}  # reranked candidate -> why selection left it out
    expansions: list[ExpansionRecord] = []
    budget_tokens: int = 0
    used_tokens: int = 0


class EvidencePackage(StrictModel):
    task_id: str
    items: list[EvidenceItem] = []  # cited source blocks
    passages: list[EvidencePassage] = []  # in source order
    gaps: list[str] = []  # content omitted or unresolved; stays a coverage gap until processed
    trace: RetrievalTrace | None = None
