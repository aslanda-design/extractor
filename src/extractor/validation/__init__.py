"""S10 + S11 - Validate a proposal and apply the acceptance policy. `validation_pipeline.py` is the whole stage in one file."""

from .validation_pipeline import Validated, validate_and_accept

__all__ = ["Validated", "validate_and_accept"]
