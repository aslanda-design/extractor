# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## State of the repo

`ARCHITECTURE.md` (v1.0) is the agreed design for a general document extraction engine (YAML spec in, structured results with evidence and explicit uncertainty out). Implementation proceeds in phases (0 foundations, 1 scope + YAML compiler, 2 parsing, 3 indexing, 4 retrieval, 5 value, 6 validation, 7 interpretation, 8 records, 9 orchestrator/recovery, 10 results, 11 hardening). Phases 0 and 1 are done; phases 2 (S03 parsing, `parsing/`) and 3 (S04 chunking + indexes, `indexing/`) are implemented and await measurement on the user's real corpus; phase 4 (S06 evidence retrieval, `retrieval/`) phase 5 (S07 value extraction, `extraction/`) and phase 6 (S10/S11 validation and acceptance, `validation/`) and phase 7 (S08 interpretation of label tasks, `interpretation/`) are implemented too; S05 planning (`planning/`) is implemented ahead of phase 9: it measures how much evidence value tasks share and, with `planning.grouping: auto` (off by default, awaiting an A/B on the corpus: `eval --extraction --grouping off,auto`), lets tasks that share evidence share one model call (`extraction/value_pipeline.py::ask_batch`; each task is still concluded, verified and validated on its own). `pipeline/extraction_pipeline.py` (the whole engine in one file) parses, indexes, retrieves evidence for every task, plans, asks the model per batch, then concludes / validates / accepts each task (`supported` only through the acceptance policy); the other stages are stubs that return `failed`. Progress and per-phase definitions live in `PROGRESS.md` (gitignored).

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

- Every pipeline is ONE readable `*_pipeline.py` file with numbered steps calling helpers in their own modules: `indexing/chunking_pipeline.py`, `retrieval/retrieval_pipeline.py`, `extraction/value_pipeline.py`, `interpretation/interpretation_pipeline.py`, `validation/validation_pipeline.py`, and the whole engine in `pipeline/extraction_pipeline.py` (S01-S13, STUB lines until built). New pipelines follow the same layout. Models, databases and retrieval depth (`retrieval.profile`) are pluggable through the runtime config; never hard-code them in a pipeline.

- Python package with a deterministic orchestrator. Models only do bounded tasks, and all vendor/model-specific behavior stays inside adapters (parsing, embedding, reranking, extraction, interpretation, verification).
- YAML describes *what* to extract; runtime config describes *which models/settings*. Never mix them.
- Three task kinds with distinct strategies: `value`, `interpretation`, `records`.
- Retrieval is hybrid (lexical + semantic + structural) → rank fusion → cross-encoder rerank → context expansion. For `records`, discovery covers the whole scope; top-k retrieval must not replace it.
- Every answer carries source evidence via stable block IDs. Parsing, retrieval and inspection are separate events in the `CoverageLedger`.
- Statuses are `supported | not_found | uncertain | failed`. Business answers (e.g. `rejected`, `pending`) are values, not statuses. A technical failure or incomplete coverage must never become `not_found`.
- Acceptance is engine policy, never model self-reported confidence. Recovery is targeted and budget-bounded.
- Corrections are stored separately from original predictions.

## Keep the code guide up to date

The user reads the code through the guide in `docs/` (git-ignored; index at `docs/README.md`, one file per folder: contracts, scope, spec, parsing, adapters, config/artifacts, pipeline/cli, evaluation, root/tests, ui). Whenever you add, rename, move or delete a file, class or function, or change what one does, update the matching `docs/` file in the same change: what each file does and what its main classes/functions are for. A new folder gets a new guide file plus a row in `docs/README.md`. Also keep `README.md` (usage) and `PROGRESS.md` (phase status) current. A change is not finished until the docs describe it.
