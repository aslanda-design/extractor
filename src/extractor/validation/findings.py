"""S10 - One way to build a finding, so every layer reads the same."""

from ..contracts.validation import Category, FindingSeverity, RecoveryHint, ValidationFinding, ValidationLayer


def finding(
    layer: ValidationLayer, category: Category, message: str, task_id: str, recovery: RecoveryHint | None = None,
) -> ValidationFinding:
    """Severity is left at INFO here: the acceptance profile decides which categories block (policy.py)."""
    return ValidationFinding(layer=layer, category=category.value, message=message, severity=FindingSeverity.INFO,
                             task_id=task_id, recovery=recovery)
