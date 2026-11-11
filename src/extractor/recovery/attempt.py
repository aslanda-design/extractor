"""S12 - What recovery works with: one try of a task, and the hands the engine lends to make another.

Recovery never extracts or validates by itself. The engine (`pipeline/extraction_pipeline.py`) gives it two functions:
`retrieve` (gather evidence again, at another depth or with a bigger budget) and `attempt` (extraction + validation + acceptance
on a package). So a recovered answer goes through exactly the same S07/S08 and S10/S11 as a first answer: a changed evidence
package can never reach acceptance without being read and validated again (ARCHITECTURE S12).
"""

from dataclasses import dataclass, field
from typing import Any, Callable

from ..contracts.candidate import ProposalOutcome, ValueProposal
from ..contracts.common import Completeness, TaskStatus
from ..contracts.evidence import EvidencePackage
from ..contracts.result import StageCost
from ..contracts.validation import AcceptanceDecision, ValidationFinding, ValidationReport
from ..records.records_pipeline import RecordsOutcome
from ..validation import Validated


@dataclass
class Attempt:
    """One try of a value or interpretation task: what was read, what was proposed, what the engine decided."""

    proposal: ValueProposal
    validated: Validated
    package: EvidencePackage
    profile: str

    @property
    def status(self) -> TaskStatus:
        return self.validated.decision.status

    @property
    def blocking(self) -> list[ValidationFinding]:
        return self.validated.decision.blocking

    @property
    def found_value(self) -> Any:
        """The value this attempt proposed (accepted or not); None when it found none."""
        return self.proposal.value if self.proposal.outcome == ProposalOutcome.FOUND else None


@dataclass
class RecordsAttempt:
    """One try of a records task: the collection read so far and the engine's decision about it."""

    outcome: RecordsOutcome
    report: ValidationReport
    decision: AcceptanceDecision
    completeness: Completeness

    @property
    def status(self) -> TaskStatus:
        return self.decision.status

    @property
    def blocking(self) -> list[ValidationFinding]:
        return self.decision.blocking


@dataclass
class RecoveryTools:
    """The hands the engine lends to recovery for ONE value / interpretation task."""

    retrieve: Callable[[str, float], EvidencePackage]  # (retrieval profile, evidence budget factor) -> a new package
    attempt: Callable[[EvidencePackage, str, dict, bool], Attempt]  # (package, profile, setting changes, ask the escalation model?) -> a new attempt
    spent: Callable[[], StageCost]  # model usage of this task so far (from the run meter)
    has_escalation: bool = False


@dataclass
class RecordsTools:
    """The hands the engine lends to recovery for ONE records task."""

    decide: Callable[[RecordsOutcome], RecordsAttempt]  # S10/S11 for a collection
    reread: Callable[..., Any]  # records_pipeline.read_document with the run's adapters, config, profile, scorer and ledger bound
    discover: Callable[..., Any]  # records/discovery.discover with the run's model and settings bound: (tagged, scope) -> Discovery
    refield: Callable[..., Any]  # (document read, found record) -> new field outcomes, with candidate verification on
    spent: Callable[[], StageCost] = field(default=lambda: StageCost())
