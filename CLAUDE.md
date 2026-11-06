# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## State of the repo

`ARCHITECTURE.md` (v1.0) is the agreed design for a general document extraction engine (YAML spec in, structured results with evidence and explicit uncertainty out). Implementation proceeds in phases (0 foundations, 1 scope + YAML compiler, 2 parsing, 3 indexing, 4 retrieval, 5 value, 6 validation, 7 interpretation, 8 records, 9 orchestrator/recovery, 10 results, 11 hardening). Phases 0 and 1 are done; phases 2 (S03 parsing, `parsing/`) and 3 (S04 chunking + indexes, `indexing/`) are implemented and await measurement on the user's real corpus; phase 4 (S06 evidence retrieval, `retrieval/`) is implemented too; `pipeline/extraction_pipeline.py` (the whole engine in one file) parses, indexes and retrieves evidence, then the extraction stages are stubs that return `failed`. Progress and per-phase definitions live in `PROGRESS.md` (gitignored).

`README.md` maps each stage to its folder under `src/extractor/`. Keep that map and the "one concern per folder" layout when adding phases; every module docstring names its stage.

## Commands

Python venv is `.venv` (Windows: `.venv\Scripts\python`).

- Install: `python -m pip install -e ".[dev]"`
- Tests: `python -m pytest` (single test: `python -m pytest tests/test_spec.py::test_dependency_cycle`); real-backend parsing tests: `python -m pytest -m integration`
- Parsing needs Java on PATH, CUDA PyTorch and `pip install -e ".[parsing,indexing]"` (see README); parsing libraries are imported only inside `parsing/backends/*`.
- UI: `ui\launch.bat` or `python ui/run.py` (separate top-level `ui/` folder; deps in `ui/requirements.txt`; it must only use the engine's public modules, never the other way round)
- CLI: `python -m extractor compile|manifest|parse|index|retrieve|run|eval` (see README.md)
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

## Constraints that are easy to violate

- Every pipeline is ONE readable `*_pipeline.py` file with numbered steps calling helpers in their own modules: `indexing/chunking_pipeline.py`, `retrieval/retrieval_pipeline.py`, and the whole engine in `pipeline/extraction_pipeline.py` (S01-S13, STUB lines until built). New pipelines follow the same layout. Models, databases and retrieval depth (`retrieval.profile`) are pluggable through the runtime config; never hard-code them in a pipeline.

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
