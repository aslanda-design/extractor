"""CoverageLedger: what was parsed / retrieved / inspected / excluded (stages S04+).

Parsing, retrieving and inspecting are separate events and must never be conflated.
"""

from enum import StrEnum

from .common import MutableModel, StrictModel


class CoverageState(StrEnum):
    PARSED = "parsed"  # task-independent
    RETRIEVED = "retrieved"  # surfaced as a candidate for a task
    INSPECTED = "inspected"  # actually read by an extraction/interpretation step
    EXCLUDED = "excluded"  # deliberately left out (with a reason)
    UNRESOLVED = "unresolved"  # could not be processed


class CoverageEntry(StrictModel):
    document_id: str
    block_id: str | None = None  # None = whole document / page-level entry
    page: int | None = None
    state: CoverageState
    task_id: str | None = None  # None for PARSED
    note: str = ""


class CoverageLedger(MutableModel):
    entries: list[CoverageEntry] = []

    def record(self, entry: CoverageEntry) -> None:
        self.entries.append(entry)

    def for_task(self, task_id: str, state: CoverageState | None = None) -> list[CoverageEntry]:
        return [
            e
            for e in self.entries
            if e.task_id == task_id and (state is None or e.state == state)
        ]

    def unresolved(self) -> list[CoverageEntry]:
        return [e for e in self.entries if e.state == CoverageState.UNRESOLVED]
