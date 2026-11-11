"""S12 - The recovery budget: attempts, model calls and model seconds one task may spend on recovery.

Recovery must be bounded, or one hard task consumes the run. The budget is checked BEFORE each attempt (an attempt that has
started is allowed to finish: cutting a call in the middle would only waste it). When it runs out the task keeps the best
result it has and the trail says so: budget exhaustion is an explicit outcome, never a made-up answer.
"""

from dataclasses import dataclass
from typing import Callable

from ..config.runtime import ExecutionConfig, RecoverySettings
from ..contracts.result import StageCost


@dataclass
class Budget:
    max_attempts: int
    max_calls: int
    max_seconds: float
    task_seconds: float | None  # `execution.max_seconds_per_task`: counts the first attempt too
    spent: Callable[[], StageCost]
    start: StageCost
    attempts: int = 0

    @classmethod
    def open(cls, recovery: RecoverySettings, execution: ExecutionConfig, spent: Callable[[], StageCost]) -> "Budget":
        return cls(execution.max_recovery_attempts, recovery.max_calls, recovery.max_seconds, execution.max_seconds_per_task, spent, spent())

    def used(self) -> tuple[int, float]:
        """(model calls, model seconds) spent by recovery so far."""
        now = self.spent()
        return now.calls - self.start.calls, round(now.seconds - self.start.seconds, 2)

    def exhausted(self) -> str | None:
        """Why no further attempt may start, or None when one may."""
        calls, seconds = self.used()
        if self.attempts >= self.max_attempts:
            return f"{self.attempts} of {self.max_attempts} recovery attempt(s) used"
        if calls >= self.max_calls:
            return f"{calls} of {self.max_calls} extra model call(s) used"
        if seconds >= self.max_seconds:
            return f"{seconds:.0f} of {self.max_seconds:.0f} extra model second(s) used"
        if self.task_seconds is not None and self.spent().seconds >= self.task_seconds:
            return f"the task used {self.spent().seconds:.0f} of {self.task_seconds:.0f} model second(s)"
        return None
