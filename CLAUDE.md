# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## State of the repo

`ARCHITECTURE.md` (v1.0) is the agreed design for a general document extraction engine (YAML spec in, structured results with evidence and explicit uncertainty out). Implementation proceeds in phases (0 foundations, 1 scope + YAML compiler, 2 parsing, 3 indexing, 4 retrieval, 5 value, 6 validation, 7 interpretation, 8 records, 9 orchestrator/recovery, 10 results, 11 hardening). Phases 0 to 10 are implemented, so every stage S01-S13 is real: `parsing/` (S03), `indexing/` (S04), `planning/` (S05 grouping, off by default), `retrieval/` (S06), `extraction/` (S07), `interpretation/` (S08), `records/` (S09), `validation/` (S10/S11), `execution/` (S05 scheduling: model-call cache, run meter, bounded concurrency), `recovery/` (S12) and, for S13, `review/` (corrections kept apart from predictions), `report/` (HTML report) and `api.py` (the public `Extractor` API). `pipeline/extraction_pipeline.py` (the whole engine in one file) parses, indexes, retrieves evidence for every task, plans, asks the model per batch, then runs each dependency layer: one attempt per task (`attempt_task`: extract or interpret, validate, accept), bounded recovery for a task that was not accepted, and a `RunReport` (results + what the run cost). Phases 2 to 8 are measured on the user's corpus; phase 9 is measured too: on the corpus the default recovery actions never trigger and the optional ones (`refield`, `deepen`) rescue nothing, so only the default actions are on (`eval --recovery`). Progress and per-phase definitions live in `PROGRESS.md` (gitignored), as do `tests/` and `docs/` (the user's choice: do not un-ignore them).

`README.md` maps each stage to its folder under `src/extractor/`. Keep that map and the "one concern per folder" layout when adding phases; every module docstring names its stage.

## Commands

Python venv is `.venv` (Windows: `.venv\Scripts\python`).

- Install: `python -m pip install -e ".[dev]"`
- Tests: `python -m pytest` (single test: `python -m pytest tests/test_spec.py::test_dependency_cycle`); real-backend parsing tests: `python -m pytest -m integration`
- Parsing needs Java on PATH, CUDA PyTorch and `pip install -e ".[parsing,indexing]"` (see README); parsing libraries are imported only inside `parsing/backends/*`.
- UI: `ui\launch.bat` or `python ui/run.py` (separate top-level `ui/` folder; deps in `ui/requirements.txt`; it must only use the engine's public modules, never the other way round)
- CLI: `python -m extractor compile|manifest|parse|index|retrieve|extract|run|eval` (see README.md)
- Human-labeled evaluation data goes in `evaluation/corpus/` (user-supplied; see its README).

Read `ARCHITECTURE.md` before implementing anything. Treat its decisions (Section 2) and stage contracts (S01–S13, Section 6) as constraints, and update it whenever a contract changes.

## The UI must show every step

Every pipeline step or capability you add must also be visible and usable in the web interface (`ui/`),
not only in the CLI, in the same change that adds it. Backend logic goes in `ui/service.py`, the route in
`ui/server.py`, the screen in `ui/static/index.html`, plus a test in `tests/` and a line in `ui/README.md`.
For each new step the UI should offer: a way to run it on an uploaded document, a visual of its output
(like the parse report and the chunk view), its settings in the Settings tab, and its quality metric in the
Corpus tab when it has one. The UI only calls public `extractor.*` modules; the engine never imports `ui/`.
A phase is not done until its UI part exists.

## The logs must show every step

The engine reports what it is doing through `src/extractor/events.py` (`emit`, `step`, `progress`, `run_scope`); the UI
shows it live in the activity strip and in the Logs tab (`/api/logs`). Whenever you add or change a pipeline stage or
step, update its logging in the same change: emit a line per numbered step with plain-language numbers (counts, cache
hit or miss, scores, budgets, gaps), wrap slow or model-calling work in `step(...)`, report loops with `progress(...)`,
use the stage code (`S03`, `S06`...) as `stage`, and use level `warn` or `error` for gaps, retries and failures. New
UI jobs in `ui/service.py` run inside `self._job("Title")` so they appear as one job. Add the stage to `STAGES` in
`ui/static/index.html` if it has a new code, add a test in `tests/test_ui_logs.py` when the event shape changes, and
describe it in `docs/` (`events.py`). Logging never changes a result and never feeds a decision.

## Constraints that are easy to violate

- Every pipeline is ONE readable `*_pipeline.py` file with numbered steps calling helpers in their own modules: `indexing/chunking_pipeline.py`, `retrieval/retrieval_pipeline.py`, `extraction/value_pipeline.py`, `interpretation/interpretation_pipeline.py`, `records/records_pipeline.py`, `validation/validation_pipeline.py`, `recovery/recovery_pipeline.py`, and the whole engine in `pipeline/extraction_pipeline.py` (S01-S13). New pipelines follow the same layout. Models, databases and retrieval depth (`retrieval.profile`) are pluggable through the runtime config; never hard-code them in a pipeline.

- Python package with a deterministic orchestrator. Models only do bounded tasks, and all vendor/model-specific behavior stays inside adapters (parsing, embedding, reranking, extraction, interpretation, verification).
- YAML describes *what* to extract; runtime config describes *which models/settings*. Never mix them.
- Three task kinds with distinct strategies: `value`, `interpretation`, `records`.
- Retrieval is hybrid (lexical + semantic + structural) → rank fusion → cross-encoder rerank → context expansion. For `records`, discovery covers the whole scope; top-k retrieval must not replace it.
- Every answer carries source evidence via stable block IDs. Parsing, retrieval and inspection are separate events in the `CoverageLedger`.
- Statuses are `supported | not_found | uncertain | failed`. Business answers (e.g. `rejected`, `pending`) are values, not statuses. A technical failure or incomplete coverage must never become `not_found`.
- Acceptance is engine policy, never model self-reported confidence. Recovery is targeted and budget-bounded: every recovery attempt goes through `attempt_task` (extraction AND validation again), an accepted result is never replaced, and a recovery action that changes how an answer is produced stays off by default until `eval --recovery` shows rescues with no harm.
- Every generative model call goes through `extraction/calling.py::call_model` (call cache, run meter, retry by failure kind, the document-is-data guard). A stage never calls `adapter.generate` itself.
- Measurements: design fixes on `split:train` / `split:validation` cases and report `split:test` separately; a difference smaller than the run-to-run spread (`eval --repeat N`) is not a result.
- Corrections are stored separately from original predictions: a review is appended beside the run (`review/`), never written into it.
- A model that is not on this machine is refused unless `privacy.allow_remote_models` is true; results list the remote capabilities.

## Keep the code guide up to date

The user reads the code through the guide in `docs/` (git-ignored; index at `docs/README.md`, one file per folder: contracts, scope, spec, parsing, adapters, config/artifacts, pipeline/cli, evaluation, root/tests, ui). Whenever you add, rename, move or delete a file, class or function, or change what one does, update the matching `docs/` file in the same change: what each file does and what its main classes/functions are for. A new folder gets a new guide file plus a row in `docs/README.md`. Also keep `README.md` (usage) and `PROGRESS.md` (phase status) current. A change is not finished until the docs describe it.
