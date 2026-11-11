"""THE PUBLIC PYTHON API: one object to run the engine, keep its runs, review them and report on them.

    from extractor.api import Extractor

    engine = Extractor("runtime.yaml")                       # or Extractor(config=RuntimeConfig(...)), or Extractor() for the defaults
    run = engine.run("spec.yaml", ["contract.pdf"])          # -> RunReport (results + what the run cost), saved in the artifact store
    engine.review(run.run_id, "maturity_date", "correct", value="2027-03-01", reviewer="ana")   # kept apart from the run
    engine.reviewed(run.run_id)                              # what a person ends up with (engine answers + reviewer decisions)
    Path("report.html").write_text(engine.report_html(run.run_id))
    engine.labels(run.run_id)                                # reviewed decisions as the `expected:` section of a corpus case

Nothing here has logic of its own: it wires `pipeline/`, `review/` and `report/` together, so the CLI, the UI and your code do the same thing.
"""

from pathlib import Path
from typing import Any

from .artifacts import ArtifactStore
from .config import RuntimeConfig, load_runtime_config
from .contracts import Review, ReviewDecision, ReviewedItem, ReviewTarget
from .contracts.result import RunReport
from .pipeline.extraction_pipeline import RUNS, run_report
from .report import render_report
from .review import ReviewStore, corrections_as_labels, make_review, reviewed_view
from .scope import DocumentRequest


class UnknownRun(KeyError):
    pass


class Extractor:
    def __init__(self, config: RuntimeConfig | Path | str | None = None):
        self.config = config if isinstance(config, RuntimeConfig) else load_runtime_config(Path(config) if config else None)
        self.store = ArtifactStore(self.config.storage.artifact_dir)
        self.reviews = ReviewStore(self.store)

    # --- runs -------------------------------------------------------------------------------------------------
    def run(self, spec: Path | str, documents: list[Path | str], profile: str | None = None) -> RunReport:
        """Documents + specification in, a RunReport out. The run is saved under its `run_id`."""
        return run_report(Path(spec), DocumentRequest.from_paths([Path(d) for d in documents]), self.config, profile)

    def runs(self) -> list[dict]:
        """Saved runs, newest first: id, when, specification, task statuses, whether any model was remote."""
        folder = self.store.root / RUNS
        out = []
        for path in folder.glob("*.json") if folder.is_dir() else []:
            r = self.run_report(path.stem)
            out.append({"run_id": r.run_id, "created_at": r.created_at, "specification": r.specification, "statuses": r.summary.statuses,
                        "tasks": len(r.results), "remote": r.execution.remote, "reviews": len(self.reviews.for_run(r.run_id))})
        return sorted(out, key=lambda x: x["created_at"], reverse=True)

    def run_report(self, run_id: str) -> RunReport:
        found = self.store.get_model(RUNS, run_id, RunReport)
        if found is None:
            raise UnknownRun(f"no saved run '{run_id}' (see `python -m extractor runs`)")
        return found

    # --- reviews ----------------------------------------------------------------------------------------------
    def review(self, run_id: str, task_id: str, decision: ReviewDecision | str, *, record: str | None = None, field: str | None = None,
               value: Any = None, note: str = "", reviewer: str = "") -> Review:
        """Record what a person decided about one task, record or record field. The run itself is not changed."""
        report = self.run_report(run_id)
        made = make_review(report, ReviewTarget(task_id=task_id, record_key=record, field_id=field), ReviewDecision(decision),
                           self.reviews.next_id(run_id), value=value, note=note, reviewer=reviewer)
        return self.reviews.add(made)

    def reviews_of(self, run_id: str) -> list[Review]:
        return self.reviews.for_run(run_id)

    def reviewed(self, run_id: str) -> list[ReviewedItem]:
        return reviewed_view(self.run_report(run_id), self.reviews.latest(run_id))

    def labels(self, run_id: str) -> dict:
        return corrections_as_labels(self.run_report(run_id), self.reviews.latest(run_id))

    # --- reports ----------------------------------------------------------------------------------------------
    def report_html(self, run_id: str) -> str:
        return render_report(self.run_report(run_id), self.reviewed(run_id))
