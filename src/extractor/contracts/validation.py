"""ValidationReport: categorized findings from stage S10."""

from enum import StrEnum

from .common import StrictModel


class ValidationLayer(StrEnum):
    STRUCTURE = "structure"
    SOURCE_GROUNDING = "source_grounding"
    NORMALIZATION = "normalization"
    SEMANTIC_SUPPORT = "semantic_support"
    CONSISTENCY = "consistency"
    COVERAGE = "coverage"
    RECORD_COMPLETENESS = "record_completeness"


class FindingSeverity(StrEnum):
    INFO = "info"
    MATERIAL = "material"  # blocks acceptance


class ValidationFinding(StrictModel):
    layer: ValidationLayer
    category: str  # actionable, e.g. "missing_context", "conflicting_values"
    message: str
    severity: FindingSeverity = FindingSeverity.MATERIAL
    task_id: str | None = None


class ValidationReport(StrictModel):
    findings: list[ValidationFinding] = []

    @property
    def has_material_issues(self) -> bool:
        return any(f.severity == FindingSeverity.MATERIAL for f in self.findings)
