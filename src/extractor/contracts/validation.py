"""ValidationReport and AcceptanceDecision: what stages S10 (validate) and S11 (accept) produce.

Findings are facts about a proposal ("the digits and the words of the amount disagree"). Whether a
finding blocks acceptance is NOT part of the finding: the acceptance profile decides that
(`validation/policy.py`), so the same findings can be judged strictly or leniently.
"""

from enum import StrEnum

from .common import StrictModel, TaskStatus


class ValidationLayer(StrEnum):
    STRUCTURE = "structure"
    SOURCE_GROUNDING = "source_grounding"
    NORMALIZATION = "normalization"
    SEMANTIC_SUPPORT = "semantic_support"
    CONSISTENCY = "consistency"
    COVERAGE = "coverage"
    RECORD_COMPLETENESS = "record_completeness"
    QUALIFICATION = "qualification"


class FindingSeverity(StrEnum):
    INFO = "info"
    MATERIAL = "material"  # blocks acceptance


class Category(StrEnum):
    """Actionable finding types. Add here, then say in `validation/policy.py` which profiles they block."""

    # structure
    BAD_TYPE = "bad_type"  # the value does not have the declared type / allowed label / cardinality
    NO_VALUE = "no_value"
    # source grounding
    NO_EVIDENCE = "no_evidence"  # the answer cites no source block
    SOURCE_MISSING = "source_missing"  # a cited block does not exist in the document
    QUOTE_NOT_IN_SOURCE = "quote_not_in_source"  # the quote was not found in the evidence
    QUOTE_APPROXIMATE = "quote_approximate"  # the quote matched only approximately
    QUOTE_IN_OTHER_PASSAGE = "quote_in_other_passage"
    QUOTE_RELOCATED = "quote_relocated"  # the quote is not verbatim; the value itself was found in the cited passage
    VALUE_NOT_IN_QUOTE = "value_not_in_quote"
    # normalization
    NOT_NORMALIZED = "not_normalized"
    SCALE_MISMATCH = "scale_mismatch"  # "NTD 12 million" read as 12
    DIGITS_WORDS_DISAGREE = "digits_words_disagree"  # "1.2 million (SAY ONE MILLION AND TWENTY THOUSAND)"
    # consistency
    REDACTED = "redacted"
    DEPENDENCY_NOT_ACCEPTED = "dependency_not_accepted"
    CANDIDATE_REJECTED = "candidate_rejected"  # other candidates were rejected by a gate / verification (info)
    # coverage
    PARSE_RISK_RELEVANT = "parse_risk_relevant"  # a damaged region matters for THIS task
    PARSE_RISK_IRRELEVANT = "parse_risk_irrelevant"  # a damaged region exists but is unrelated to this task (info)
    PARSE_ISSUE_ON_CITED_PAGE = "parse_issue_on_cited_page"
    BLANK_PAGE = "blank_page"  # empty after the whole OCR retry ladder: probably blank
    CONTENT_OMITTED = "content_omitted"  # evidence left out because of the token budget
    MODEL_CALL_FAILED = "model_call_failed"
    UNRESOLVED_REFERENCE = "unresolved_reference"  # "see Section 9" inside the document that was not found
    UNRESOLVED_ATTACHMENT = "unresolved_attachment"  # Schedule / Exhibit not among the supplied files
    # record collections (S09)
    NUMBERING_GAP = "numbering_gap"  # (a) (b) (d): a member of a numbered sequence is missing
    POSSIBLE_MISSED_RECORD = "possible_missed_record"  # numbered text inside the scope that no record covers
    OUTPUT_TRUNCATED = "output_truncated"  # the model filled its answer limit: more records may exist
    DISCOVERY_FAILED = "discovery_failed"  # a discovery call failed: part of the scope was never read
    DUPLICATE_KEY = "duplicate_key"  # the same identity found twice in different places
    PARSE_ISSUE_IN_SCOPE = "parse_issue_in_scope"  # unresolved parsing problem on a page of the scope
    SCOPE_FALLBACK = "scope_fallback"  # the region could not be located, the whole document was searched (info)
    BOUNDARY_NOT_LOCATED = "boundary_not_located"  # a record start could not be found in its block (info)
    RECORD_FIELD_NOT_ACCEPTED = "record_field_not_accepted"  # a required field of a record was not accepted
    RECORD_REJECTED = "record_rejected"  # listed by discovery, then removed because it does not match the record definition (info)
    NO_RECORDS = "no_records"  # nothing found: only an empty answer if the audit was complete
    # semantic
    SEMANTIC_UNSUPPORTED = "semantic_unsupported"
    SEMANTIC_CHECK_FAILED = "semantic_check_failed"
    # recovery (S12)
    RECOVERY_BUDGET_EXHAUSTED = "recovery_budget_exhausted"  # recovery stopped because attempts, calls or seconds ran out (info)
    RECOVERY_DISAGREEMENT = "recovery_disagreement"  # a recovery attempt found a different value than an earlier attempt (info)
    # qualification
    MODEL_NOT_QUALIFIED = "model_not_qualified"


class RecoveryHint(StrictModel):
    """What recovery (S12, `recovery/`) could do about a finding."""

    action: str  # reparse_region | inspect_passages | retry_call | resolve_reference
    document_id: str | None = None
    page: int | None = None
    passage_ids: list[str] = []
    reason: str = ""


class ValidationFinding(StrictModel):
    layer: ValidationLayer
    category: str  # a `Category` value (a string so other stages may add their own)
    message: str
    severity: FindingSeverity = FindingSeverity.MATERIAL
    task_id: str | None = None
    recovery: RecoveryHint | None = None


class ValidationReport(StrictModel):
    findings: list[ValidationFinding] = []

    @property
    def has_material_issues(self) -> bool:
        return any(f.severity == FindingSeverity.MATERIAL for f in self.findings)

    @property
    def material(self) -> list[ValidationFinding]:
        return [f for f in self.findings if f.severity == FindingSeverity.MATERIAL]


class AcceptanceDecision(StrictModel):
    """The engine's decision for one task (never a model confidence)."""

    status: TaskStatus
    policy: str  # acceptance profile that decided
    reasons: list[str] = []  # plain language, most important first
    blocking: list[ValidationFinding] = []
    hints: list[RecoveryHint] = []  # what recovery could try
