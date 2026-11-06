# Extractor

General document extraction engine. The design is in [ARCHITECTURE.md](ARCHITECTURE.md);
this file is the map from that design to the code.

## Setup

```
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m pytest
```

## Commands

```
python -m extractor compile examples/spec.example.yaml      # validate a spec, show execution order
python -m extractor manifest --docs a.pdf b.pdf             # S01 manifest (hashes, scope)
python -m extractor run examples/spec.example.yaml --docs a.pdf
python -m extractor eval [--score]                          # validate / score evaluation/corpus
```

## Where things live

Each folder is one concern; a stage's code never reaches into another stage's folder
except through `contracts/`.

| Folder (`src/extractor/`) | Stage | What it owns |
| --- | --- | --- |
| `contracts/` | all | Data shapes passed between stages, one file per family. Start here. |
| `scope/` | S01 | Request -> `DocumentManifest` (hashes, relationships, scope, precedence flag). |
| `spec/` | S02 | YAML -> checks -> `CompiledSpecification` (tasks + dependency graph). |
| `adapters/` | S03, S06-S10 | Interfaces for parser / embedder / reranker / extractor / interpreter / verifier, plus the registry and fakes. **The only place model-specific code goes.** |
| `config/` | - | Runtime settings (which models, limits, storage). Separate from the YAML. |
| `artifacts/` | - | Fingerprints and the on-disk artifact store (caching, originals). |
| `pipeline/` | S05 | Wires stages together. Stages not built yet are marked stubs in `run.py`. |
| `evaluation/` | - | Loads the labeled corpus and scores engine output. |
| `cli.py` | - | Command line. |

Planned folders (created by their phase): `parsing/` (S03), `indexing/` (S04),
`retrieval/` (S06), `strategies/` (S07-S09), `validation/` (S10-S11), `recovery/` (S12),
`results/` (S13).

## Rules of the codebase

- Adding a model/backend = a new adapter class + `register_adapter(...)`. Nothing else changes.
- Extraction YAML says *what*; `RuntimeConfig` says *which model / limits*. Never mix them.
- A stage that did not run is `failed`, never `not_found`.
- Every module docstring says which stage it implements.

## Your evaluation data

Add labeled documents under `evaluation/corpus/` - see [its README](evaluation/corpus/README.md).
