# Extractor

A reusable engine that takes documents plus a YAML specification and returns structured
results with source evidence and explicit uncertainty. The design lives in
[ARCHITECTURE.md](ARCHITECTURE.md); this file explains how to use the engine and what
each part does.

> **Status:** phases 0-1 (foundations, scope, YAML compiler) are complete; phases 2
> (parsing), 3 (chunking + indexes) and 4 (evidence retrieval) are implemented. The extraction
> strategies (value / interpretation / records) and validation are not built yet, so `run`
> returns `failed` for every task (after parsing, indexing and retrieving evidence for it).
> See [PROGRESS.md](PROGRESS.md) for the plan.

## 1. Setup

Requirements: Python 3.11+, Java 11+ on the PATH (used by the native PDF parser), and an
NVIDIA GPU for fast OCR (CPU works, slower).

```
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"

# Real parser (phase 2). Install CUDA PyTorch first, then the parsing extras:
.venv\Scripts\python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
.venv\Scripts\python -m pip install -e ".[parsing,indexing]"
```

Tests:

```
.venv\Scripts\python -m pytest                    # fast, no models needed
.venv\Scripts\python -m pytest -m integration     # real backends on synthetic PDFs (slow)
```

The first OCR run downloads models into `<artifact_dir>/models/` (about 1.5 minutes);
after that everything works offline.

## 2. Quick tour

**Prefer a graphical interface?** It shows every step of the engine (parse, chunk and index, settings, corpus metrics). Run `ui\launch.bat` (after `pip install -r ui
equirements.txt`): upload documents, parse and inspect them, edit and validate specs, change settings and check your corpus from the browser. See [ui/README.md](ui/README.md).

Or use the command line:

```
# 1. Check an extraction spec: errors, review flags, execution order
python -m extractor compile examples/spec.example.yaml

# 2. See how documents are scoped (hashes, ids, relationships, precedence flag)
python -m extractor manifest --docs contract.pdf amendment.pdf

# 3. Parse a document and inspect the result
python -m extractor parse contract.pdf
#    -> .extractor_artifacts/reports/contract.report.html   (open in a browser)
#    -> .extractor_artifacts/reports/contract.parsed.json

# 4. Chunk and index it: sections, passages, both indexes
python -m extractor index contract.pdf --query "when is the loan repaid"
#    -> .extractor_artifacts/reports/contract.index.html    (passage boundaries drawn on the pages)

# 5. See every step of evidence retrieval for one task, at a chosen depth
python -m extractor retrieve examples/spec.example.yaml maturity_date --docs contract.pdf --profile deep

# 6. Validate your labeled corpus, and measure parsing / chunking / retrieval quality on it
python -m extractor eval
python -m extractor eval --parsing
python -m extractor eval --indexing
python -m extractor eval --retrieval --profile light,standard,deep

# 7. Run the whole engine (S01-S04 and S06 real, the rest are stubs returning `failed`)
python -m extractor run examples/spec.example.yaml --docs contract.pdf --profile standard
```

## 3. The two inputs you write

### 3.1 Extraction specification (YAML): *what* to extract

See `examples/spec.example.yaml` for a complete file. Each task has an `id`, a `kind` and
a `description`:

| `kind` | Question it answers |
| --- | --- |
| `value` | What is the requested value, if present? |
| `interpretation` | What conclusion do the documents support under my criteria? |
| `records` | What are *all* the matching occurrences, and their fields? |

Useful keys: `output` (`type`, `labels`, `cardinality`, `format`), `scope` (documents,
sections, entity, time), `rules` (`normalization`, `derivation`, `decision_criteria`,
`precedence`, `on_absent`), `depends_on`, and for records a `record:` section
(`identity`, `boundaries`, `parent`, `fields`).

`python -m extractor compile spec.yaml` reports **every** error at once with its YAML path.
Vague declarations (an interpretation with no criteria, records with no boundaries) are
not rejected and not guessed: they are kept as *review* flags on that task.
Runtime settings (model names, thresholds) are rejected inside the spec on purpose.

### 3.2 Runtime configuration (YAML, optional): *how* to run

Which models, limits and thresholds. Pass it with `--config runtime.yaml`:

```yaml
models:
  parsing:
    name: layered            # "fake" (no-op) or "layered" (OpenDataLoader + Docling)
    params: {languages: [en], device: auto}   # device: auto | cuda | cpu
  embedding:                 # swap the model here: nothing else changes
    name: sentence_transformers   # any Hugging Face / local model; or "openai_compatible" for an API
    params: {model: Qwen/Qwen3-Embedding-0.6B, device: auto}
  reranking:
    name: cross_encoder           # or "http_rerank" for Cohere / Jina / vLLM
    params: {model: tomaarsen/Qwen3-Reranker-0.6B-seq-cls}
  # extraction / interpretation / verification: "fake" for now

indexing:
  chunking: {max_tokens: 350, min_tokens: 40}   # S04 passage size rules (tokens of the embedding model)
  lexical_store: {name: sqlite_fts5}            # swap the databases here
  vector_store: {name: numpy}

retrieval:                                      # S06 depth of extraction
  profile: standard                             # light | standard | deep | your own
  profiles:
    thorough_legal: {extends: deep, budget_tokens: 12000}

parsing:                     # S03 thresholds - tune these against your scans
  min_native_chars: 30       # fewer characters on a page => no usable text layer => OCR
  max_garbage_ratio: 0.03    # share of replacement / private-use characters => OCR
  scan_image_coverage: 0.6   # page mostly covered by one picture ...
  scan_max_chars: 150        # ... with little text on top => OCR
  region_min_image_area: 0.05  # OCR pictures at least this share of a native page
  ocr_dpi: 200
  ocr_hires_dpi: 300         # last rung of the retry ladder
  use_cache: true

execution: {max_concurrency: 1, max_recovery_attempts: 2}
storage: {artifact_dir: .extractor_artifacts}
```

Without `--config`, `parse`, `index`, `run` and `eval --parsing/--indexing` use the real stack
(`layered` parser + the default embedding model) instead of the fakes.
A fully commented example is in `examples/runtime.example.yaml`.

### 3.3 Document request (YAML, optional)

For several documents with relationships, section limits or precedence rules
(`manifest --request request.yaml`, `run --request ...`):

```yaml
documents:
  - path: loan.pdf
  - path: amendment_1.pdf
    relationships:
      - {kind: amendment, target: loan.pdf}   # attachment | amendment | replacement
    sections: ["Article 5"]
as_of: 2026-01-01
precedence_rules:
  - "Amendments override the base agreement."
```

Upload order never decides precedence. Several documents with no relationships and no
rules produce `precedence_resolved: false`, so affected tasks stay uncertain.

## 4. The parts of the engine

Each folder under `src/extractor/` is one concern. A stage reaches another only through the
data shapes in `contracts/`. Every module's first docstring line names its stage.

| Folder | Stage | What it does | Where to edit |
| --- | --- | --- | --- |
| `contracts/` | all | Data shapes passed between stages (manifest, task, document, evidence, result...). One file per family. **Start here to understand the engine.** | Add a field to the right file; update ARCHITECTURE.md if a contract changes |
| `scope/` | S01 | Request -> immutable `DocumentManifest`: content hashes, stable ids, relationships, section limits, precedence flag; keeps original files. `resolver.py` maps a task's document references to ids. | `manifest_builder.py` |
| `spec/` | S02 | YAML -> checks -> `CompiledSpecification` (normalized tasks + dependency graph with parallel layers). `loader.py` shape, `checks.py` meaning, `dependency_graph.py` ordering, `compiler.py` glue. | A new validity rule: `checks.py` |
| `parsing/` | S03 | Native text first, OCR where a page needs it. `routing.py` decides per page (and per picture region), `detectors.py` finds problems, `parsing_pipeline.py` is the whole pipeline in one file (native -> completeness check -> OCR ladder), `reconcile.py` compares the layout parse with the PDF's own text layer and restores dropped words, `normalize.py` assigns block ids, `report.py` writes the HTML inspection page, `metrics.py` scores against your labels. | Thresholds: runtime config. Logic: `routing.py`, `detectors.py` |
| `parsing/backends/` | S03 | The only files that import a parsing library: `opendataloader.py` (native text/structure), `docling_ocr.py` (layout + OCR + tables), `pdfium_renderer.py` (page images). Swap a library by writing another class with the same methods (`backends/base.py`). | One file per library |
| `adapters/` | S03, S04, S06-S10 | Interfaces for parser / embedder / reranker / extractor / interpreter / verifier, the registry that builds them from the runtime config, no-op fakes, the embedding adapters `embedding_sentence_transformers.py` (any local model) and `embedding_openai.py` (any `/v1/embeddings` endpoint), and the reranker adapters `rerank_cross_encoder.py` (any CrossEncoder) and `rerank_http.py` (Cohere / Jina / vLLM `/rerank`). **The only place model-specific code may live.** | New backend: adapter class + `register_adapter(...)` in `adapters/__init__.py` |
| `config/` | - | Runtime settings (`RuntimeConfig`): models, limits, parsing thresholds, storage. | `runtime.py` |
| `artifacts/` | - | SHA-256 fingerprints and the on-disk store (parsed documents, raw parser output, manifests, original files). Keys include document hash + settings, so a changed input or setting never hits stale cache. | `store.py` |
| `indexing/` | S04 | **`chunking_pipeline.py` is the whole chunking pipeline in one readable file** (numbered steps, each calling a helper). Helpers: `structure.py` heading hierarchy, `continuation.py` page-break joins, `units.py` / `search_text.py` search view with a map back to the source, `sizing.py` merge/cut rules, `passages.py`, `references.py`. `builder.py` = cache + indexes + coverage ledger. `report.py` = HTML view. | Chunking behaviour: `chunking_pipeline.py` and the helper it calls; sizes: runtime config |
| `indexing/stores/` | S04 | Pluggable databases: `base.py` interfaces (`LexicalIndex`, `VectorStore`), `sqlite_fts5.py` (BM25), `numpy_store.py` (exact cosine). | New database: subclass + register in `stores/__init__.py` |
| `retrieval/` | S06 | **`retrieval_pipeline.py` is the whole evidence-retrieval pipeline in one readable file** (queries -> candidates -> fusion -> rerank -> selection -> expansion -> packaging). Helpers: `queries.py`, `candidates.py`, `fusion.py`, `rerank.py`, `selection.py`, `definitions.py`, `expansion.py`, `packaging.py`; `report.py` prints a run's trace. | Retrieval behaviour: `retrieval_pipeline.py` and its helper; depth: `retrieval.profile(s)` in the runtime config |
| `pipeline/` | all | **`extraction_pipeline.py` is the whole engine in one readable file**: S01-S13 as numbered calls, real or marked STUB. Each phase turns its STUB line into a call. | `extraction_pipeline.py` |
| `evaluation/` | - | Loads and validates your labeled corpus (`corpus.py`), scores engine output (`harness.py`), measures parsing (`parsing_eval.py`) and chunking (`indexing_eval.py`) quality. | `corpus.py` for the label format |
| `cli.py` | - | The command line. |

The graphical interface is **not** part of this package: it lives in the separate top-level `ui/` folder and only calls the modules above. |

### Result vocabulary (applies everywhere)

Task status: `supported`, `not_found`, `uncertain`, `failed`. A business answer such as
`rejected` or `pending` is a **value**, never a status. A stage that did not run, or a
page that could not be read, is `failed` / an unresolved coverage limitation, never
`not_found`. Records carry a separate completeness: `checked`, `partial`, `unknown`.

### How parsing decides (S03)

1. OpenDataLoader reads the PDF's text layer: headings, paragraphs, tables, picture boxes,
   all with bounding boxes. (Header/footer regions are kept, not dropped.)
2. Each page is routed from three cheap signals: characters in the text layer, share of
   corrupted characters, share of the page covered by a picture. Bad text layer or
   scanned page => Docling OCR.
3. On a page that stays native, a large picture with no text over it is OCR'd on its own
   and its blocks are merged in reading order.
4. Reading-order or table problems trigger an OCR retry; if it fixes them they are marked
   resolved. Anything still broken stays `resolved: false` => a coverage limitation.
5. Every block gets the id `<document_id>:p<page>:b<n>` and a bounding box in PDF points
   with the origin at the top-left, so evidence can be drawn on the original page.

### How chunking works (S04)

Open `src/extractor/indexing/chunking_pipeline.py`: it lists the steps in order.

1. Headings become a hierarchy (`heading_path`, `parent_id`). Depth comes from the parser's
   level, or from numbering ("1.2.3", "Article V") when the parser's levels are flat.
2. Paragraphs and tables that continue across a page break are linked (source blocks stay
   separate; only the passages are built over the chain; a repeated table header is dropped).
3. Blocks become units, then the search view is built: normalized text (ligatures,
   de-hyphenation, whitespace) with a character-level map back to the source text.
4. Units are grouped by section. A section is one passage up to `max_tokens`; bigger ones are
   cut at sentence boundaries, tiny ones (< `min_tokens`) merge with a neighbour, a lone heading
   always joins the text it introduces, tables are their own passages (long ones split by rows
   with the header row repeated). No overlap: passages know their neighbours instead.
5. Explicit references ("Section 5.2", "Schedule A") are resolved to heading blocks;
   unresolvable ones are recorded as unresolved, never guessed.

Each passage keeps its original text and exact source ranges (`indexing/provenance.py` maps any
part of a passage back to block, page and bounding box). Passage size is measured with the
embedding model's own tokenizer.

### Swapping the embedding model or the databases

Both are chosen in the runtime config, never in code (see `examples/runtime.example.yaml`):

- **Embedding model:** `models.embedding.params.model` (a bigger Qwen3-Embedding, a BGE model,
  a local folder), or `name: openai_compatible` for an API / vLLM server. The index cache key
  contains the model identity, so the next run re-chunks (sizes depend on its tokenizer) and
  re-embeds automatically; the old index is left untouched.
- **Databases:** `indexing.vector_store` / `indexing.lexical_store`. To add FAISS, LanceDB,
  pgvector, Qdrant...: subclass `VectorStore` or `LexicalIndex` (`indexing/stores/base.py`),
  register it in `indexing/stores/__init__.py`, select it by name. Vectors are cached separately,
  so changing only the database does not re-run the embedding model.

### How evidence retrieval works (S06)

Open `src/extractor/retrieval/retrieval_pipeline.py`: `gather_evidence` lists the steps in order.

1. **Queries:** the task becomes keyword queries (BM25), natural-language queries (one extra per
   allowed label for interpretation tasks), a reranker statement and qualifier keywords.
2. **Candidates:** lexical (BM25), vector (embeddings) and structural (heading / table-header word
   matches) lists, plus a qualifier pass (exceptions, conditions, amendments).
3. **Fusion:** reciprocal rank fusion, never adding raw scores; provenance kept per passage.
4. **Rerank:** a cross-encoder judges (task, passage) pairs. Documents of up to
   `short_doc_passages` passages are reranked whole, so stage one cannot lose evidence.
5. **Select:** adaptive cut at `score_drop_ratio` x the best score, near-duplicate removal (same
   words AND same numbers, so "10%" vs "12%" clauses both survive), per-section and per-document
   diversity, reserved slots for qualifier candidates.
6. **Expand:** neighbours, the sections selected passages point at ("see Section 5.2"), and the
   definitions of defined terms they use (`"Maturity Date" means ...`). Added passages are scored too;
   strong ones are promoted to primary evidence.
7. **Package:** source order, token budget, provisional roles (supports / qualifies / contextualizes);
   whatever does not fit or cannot be resolved becomes an explicit **gap**.

Retrieval never concludes anything. A weak result is low scores plus gaps, not `not_found`.
`python -m extractor retrieve spec.yaml <task> --docs ...` prints all of this for one task.

**Depth is a profile**, not code: `retrieval.profile` (or `--profile`) selects a bundle of
`RetrievalSettings`: candidates per list, rerank pool, passages kept, score cutoff, token budget,
neighbours, reference / definition / qualifier switches, short-document limit, per-source on/off.
`light` is cheap and fast, `standard` the default, `deep` wide pools and two expansion rounds; define
your own under `retrieval.profiles` (optionally `extends:` another one). These are runtime
settings; they cannot appear in the task YAML.

Image files (png, jpg, tiff) are treated as one OCR page. Other formats currently return an
explicit `not_parsed` issue.

## 5. Your evaluation corpus

Put human-labeled data under `evaluation/corpus/` (documents, specs, one YAML per case with
expected answers). Start from `evaluation/corpus/_template/` and read
[evaluation/corpus/README.md](evaluation/corpus/README.md) for the labeling rules.

- `python -m extractor eval` validates it and lists every problem at once.
- `python -m extractor eval --parsing` parses each document and reports: quote recall
  (labeled quote found in the parse), page accuracy (found on the labeled page), OCR page
  count, empty/corrupted pages and unresolved issues.
- `python -m extractor eval --indexing` chunks each document and reports how many labeled
  quotes sit inside ONE passage (chunk boundaries respecting the evidence), how many were cut
  across passages, how many are missing (a parsing problem), passage size stats and any
  structural-invariant violations.
- `python -m extractor eval --retrieval [--profile light,standard,deep] [--variants ...]` reports,
  per profile and ablation (full, stage_one, no_rerank, lexical_only, vector_only, no_structural,
  no_qualifier, no_expansion): recall of your labeled evidence at the candidate, selected and package
  level, recall of `contradicts` / `qualifies` evidence on its own, mean passages and tokens per task.
- `python -m extractor eval --score` scores full engine output (meaningful after phase 5).

## 6. Rules of the codebase

- Adding a model or library = a new adapter/backend class + registration. Nothing else changes.
- The YAML says *what*; `RuntimeConfig` says *which model / limits*. Never mix them.
- Heavy libraries are imported only inside `parsing/backends/*`, the embedding adapters and the store modules, and load lazily.
- Every pipeline is ONE readable `*_pipeline.py` file with numbered steps calling helpers in their own
  modules (`chunking_pipeline.py`, `retrieval_pipeline.py`, and the whole engine in `extraction_pipeline.py`).
  Models, databases and depth are never hard-coded in them.
- Everything crossing a stage boundary is a type from `contracts/`.
- Update ARCHITECTURE.md when a contract changes, and PROGRESS.md as phases complete.
