# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## State of the repo

`ARCHITECTURE.md` (v1.0) is the agreed design for a general document extraction engine (YAML spec in, structured results with evidence and explicit uncertainty out). Implementation proceeds in phases (0 foundations, 1 scope + YAML compiler, 2 parsing, 3 indexing, 4 retrieval, 5 value, 6 validation, 7 interpretation, 8 records, 9 orchestrator/recovery, 10 results, 11 hardening). Phases 0 and 1 are done; stages S03+ in `pipeline/run.py` are stubs that return `failed`.

`README.md` maps each stage to its folder under `src/extractor/`. Keep that map and the "one concern per folder" layout when adding phases; every module docstring names its stage.

## Commands

Python venv is `.venv` (Windows: `.venv\Scripts\python`).

- Install: `python -m pip install -e ".[dev]"`
- Tests: `python -m pytest` (single test: `python -m pytest tests/test_spec.py::test_dependency_cycle`)
- CLI: `python -m extractor compile|manifest|run|eval` (see README.md)
- Human-labeled evaluation data goes in `evaluation/corpus/` (user-supplied; see its README).

Read `ARCHITECTURE.md` before implementing anything. Treat its decisions (Section 2) and stage contracts (S01–S13, Section 6) as constraints, and update it whenever a contract changes.

## Constraints that are easy to violate

- Python package with a deterministic orchestrator. Models only do bounded tasks, and all vendor/model-specific behavior stays inside adapters (parsing, embedding, reranking, extraction, interpretation, verification).
- YAML describes *what* to extract; runtime config describes *which models/settings*. Never mix them.
- Three task kinds with distinct strategies: `value`, `interpretation`, `records`.
- Retrieval is hybrid (lexical + semantic + structural) → rank fusion → cross-encoder rerank → context expansion. For `records`, discovery covers the whole scope; top-k retrieval must not replace it.
- Every answer carries source evidence via stable block IDs. Parsing, retrieval and inspection are separate events in the `CoverageLedger`.
- Statuses are `supported | not_found | uncertain | failed`. Business answers (e.g. `rejected`, `pending`) are values, not statuses. A technical failure or incomplete coverage must never become `not_found`.
- Acceptance is engine policy, never model self-reported confidence. Recovery is targeted and budget-bounded.
- Corrections are stored separately from original predictions.
