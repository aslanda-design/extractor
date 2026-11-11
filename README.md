# Extractor

A document extraction engine: **documents + a YAML specification in, structured results with source evidence and explicit
uncertainty out.** It runs entirely on local models (a laptop GPU is enough) and any model can be swapped through configuration.

The idea it is built on: **a model proposes, the engine verifies and decides.** A language model is never trusted for its
confidence. Every answer must be quoted from the document, the quote is located by code, and a policy the engine owns decides
whether the answer is accepted. When the engine cannot be sure it says `uncertain`, and it says `not_found` only after the
inspection it was asked to do is complete. A technical failure is `failed`, never "not found".

```
documents ─┐                                                       ┌─ supported   value + the source blocks that prove it
           ├─ scope ─ parse ─ index ─ retrieve ─ extract ─ validate ─ accept ─ recover ─┤  not_found   inspection complete, nothing states it
YAML spec ─┘   S01    S03     S04      S06     S07-S09     S10      S11     S12     └─ uncertain / failed, with the reasons
```

Three kinds of task, each with its own strategy:

| Kind | Question | How |
| --- | --- | --- |
| `value` | What is the value, if present? | hybrid retrieval, the model lists candidates with quotes, code normalizes and reconciles them |
| `interpretation` | What do the documents support under my criteria? | evidence by label, exact arithmetic by engine tools, a judge that can only downgrade |
| `records` | What are ALL the occurrences? | the whole scope is read (never top-k), the model points and the engine cuts, an audit checks completeness |

## Results so far

Measured on a private corpus of **10 commercial contracts** (digital and scanned) with human-reviewed labels, using a local
**Qwen3 8B** model (Ollama) on an RTX 3060, standard settings:

| Task kind | Labeled | Accepted automatically | Accepted answers that are right | False accepts |
| --- | --- | --- | --- | --- |
| value | 100 tasks | 79% | 97% | 2 |
| value + interpretation, end to end | 130 tasks | 82% | 98% | 2 |
| of which the held-out test split | 39 tasks | 82% | 97% | 1 |
| records | 11 tasks, 116 records | record recall 89%, precision 87%, field exact 71% | 2 of 11 collections fully accepted | - |

How to read these numbers honestly: they are from one document family, the fixes were tuned on these same documents (the
test split is reported separately and is close to the others), and only one model was measured. Single runs are
deterministic with this setup (three uncached runs of one case were identical), so a difference between two runs is a
real difference, not noise. Recovery (S12) was measured: the default actions never triggered on this corpus, and the
optional ones (`refield`, `deepen`) rescued nothing, so they stay off. The corpus itself is not in the repository (the
documents are not mine to publish), so the numbers cannot be reproduced from a clone yet.

## What is in the box

- **One readable file per pipeline** (`*_pipeline.py`: numbered steps, each a call into its own module), and the whole engine in
  `src/extractor/pipeline/extraction_pipeline.py`.
- **Adapters** for parsing, embedding, reranking and chat models; the engine never names a model or a vendor.
- **Evidence by stable block ids**, a coverage ledger (parsed / retrieved / inspected are separate facts), four statuses.
- **Acceptance as policy**: validation findings are facts; a profile (`strict` / `standard` / `lenient`) says which block.
- **Bounded recovery**: a task that was not accepted gets targeted second tries that go through the same validation, within a
  budget, and every attempt is recorded.
- **A resumable run**: finished model calls are cached by content, so a crashed run repeats only what is missing.
- **An evaluation harness** that says not only how often the engine is wrong but which stage caused each error.
- **Results you can keep and review**: every run has a reproducible id and is saved; a reviewer's accept / correct / reject is stored beside the engine's answer, never over it, and can be exported as evaluation labels; a self-contained HTML report explains every answer.
- **Documents stay local by default**: a hosted model needs an explicit `privacy.allow_remote_models: true` and is listed in every result.
- **A web interface** that shows every step, a CLI and a small Python API (`from extractor.api import Extractor`). 440 unit tests (a scripted model stands in for the real one).

The design is in [ARCHITECTURE.md](ARCHITECTURE.md) and the reasons behind its main choices are in [DECISIONS.md](DECISIONS.md). The rest of this file is the user guide and the reference.

> **Status:** every stage S01-S13 is implemented. Phases done: foundations, scope, YAML compiler, parsing, indexing, retrieval,
> value extraction, validation and acceptance, interpretation, records, orchestration and recovery, results and review. Next: a public
> benchmark, a second model and hardening (phase 11).

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

**Prefer a graphical interface?** It shows every step of the engine (parse, chunk and index, settings, corpus metrics). Run `ui\launch.bat` (after `pip install -r ui/requirements.txt`): upload documents, parse and inspect them, edit and validate specs, change settings and check your corpus from the browser. See [ui/README.md](ui/README.md).

Or use the command line:

```
# 1. Check an extraction spec: errors, review flags, execution order
python -m extractor compile examples/spec.example.yaml

# 1b. Draft a spec from plain language: web interface only (New extraction > "Describe it"; API POST /api/specs/draft).

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

# 6. Extract the value of one task: candidates, quotes, reconciliation, every model call
#    (needs the extraction model, see 3.2: `ollama pull qwen3:8b` and models.extraction in your --config)
python -m extractor extract examples/spec.example.yaml maturity_date --docs contract.pdf --config runtime.yaml

# 7. Validate your labeled corpus, and measure parsing / chunking / retrieval / extraction quality on it
python -m extractor eval
python -m extractor eval --parsing
python -m extractor eval --indexing
python -m extractor eval --retrieval --profile light,standard,deep
python -m extractor eval --overlap --profile standard                   # how much evidence the tasks of each case share (no chat model)
python -m extractor eval --extraction --profile light,standard,deep --config runtime.yaml
python -m extractor eval --extraction --grouping off,auto               # A/B: one call per task vs tasks that share evidence sharing a call
python -m extractor eval --extraction --kinds value --tags split:test   # only some field types / case tags
python -m extractor eval --extraction --cases business_01,business_05   # only some extraction projects (cases)

# 8. Measure what the engine ACCEPTS (S10/S11): automation, accepted-answer precision, false accepts / absences
python -m extractor eval --acceptance --save runs/a1 --config runtime.yaml     # runs everything, stores each proposal
python -m extractor eval --acceptance --from runs/a1 --config runtime.yaml     # re-runs only validation: no model, seconds
python -m extractor eval --acceptance --from runs/a1 --qualify                 # write a qualification record if the thresholds are met

# 9. Run the whole engine (S01-S13). --out writes the stable result file: every task result + what the run cost
python -m extractor run examples/spec.example.yaml --docs contract.pdf --profile standard --out result.json --config runtime.yaml
python -m extractor schema                                              # the JSON Schema of that file
#    A run that stops half way resumes: run the same command again and finished model calls are read from the cache.

# 10. Measure recovery (S12): what each action rescued, harmed and cost; and the run-to-run spread of a measure
python -m extractor eval --recovery --save runs/r1 --config runtime.yaml
python -m extractor eval --acceptance --repeat 3 --config runtime.yaml  # 3 runs without the call cache
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

#### Writing task descriptions that work better

The `description` (and `rules`) of a value task is the only thing the model knows about the field. Rewriting 13 descriptions
of the labeled corpus fixed 11 of them with a local 8B model and broke none (same model, same evidence). The patterns that worked:

1. **Say what the answer looks like and what it must NOT be.** "The numeric FCC Facility ID (digits only, printed as
   'Facility ID No. ...'). The call sign (WQHT-FM) is NOT the ID." Models return the nearest thing they recognise; naming the
   look-alike they must skip (a call sign, a defined term such as "Distributor", a title) fixed identifiers and names.
2. **Name the place to read and the thing it is confused with.** "Date of the ORIGINAL agreement, from a recital about that
   agreement; it is NOT the date of this amendment." "Jurisdiction in the governing-law clause, not where a party is incorporated."
   Documents repeat the same kind of value many times (dates, parties, amounts); the description must say which one.
3. **Say what counts as absence.** "If no date is labelled 'Effective Date', report no candidate." "An email address (contains
   '@') ... if the clause gives only a postal address, report no candidate." Without this the model fills the field with the
   nearest value (the date the agreement was *made*, a company name instead of an email). This removed every false value it was applied to.
4. **When the answer is part of a phrase, give its form.** "The language name only (such as 'English'), without the word
   'version'." Without it the model copies the whole phrase ("Chinese version").
5. **Keep the original anchors when you reword.** Clause numbers, party names and the two things being compared must stay
   ("Language version given priority by clause 12.7"). A rewrite that dropped them made the model answer "not found".
6. **Write each field for its own contract.** The same field name in two specs needs its own wording; one shared sentence
   regressed a spec whose original text said "return state name only".

What wording cannot fix: text lost by OCR, and amounts in a table whose unit sits in a header ("NTD million"); those are
parsing problems. Measure every rewording with `python -m extractor eval --extraction` before keeping it.

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
  extraction:                    # the model that reads the evidence (S07). "fake" = no model: tasks end `failed`
    name: ollama_chat            # local Ollama; or "openai_compatible_chat" for any hosted / vLLM / LM Studio endpoint
    params: {model: "qwen3:8b", context_tokens: 16384}
  verification:                  # optional semantic check at S10 (acceptance.semantic_check); may be a bigger model
    name: fake                   # or ollama_chat / openai_compatible_chat, same params as extraction
  # interpretation: "fake" for now

indexing:
  chunking: {max_tokens: 350, min_tokens: 40}   # S04 passage size rules (tokens of the embedding model)
  lexical_store: {name: sqlite_fts5}            # swap the databases here
  vector_store: {name: numpy}

extraction:                                     # S07 knobs (WHICH model is models.extraction)
  max_candidates: 8             # values the model may report per call
  retries: 1                    # extra attempts when a call fails
  choose_with_model: true       # the model applies the task precedence rules to conflicting values
  unresolved_references_block_absence: true   # "see Section 9" (inside the document) that cannot be found keeps an empty answer `uncertain`
  unresolved_attachments_block_absence: false # "Schedule A" / "Exhibit 3" missing from the supplied files does not block `not_found`

acceptance:                                     # S10/S11: when is an answer ACCEPTED (never a model confidence)
  profile: standard             # strict | standard | lenient (see "How validation and acceptance work")
  risk_relevance_ratio: 0.5     # a damaged region blocks "not found" when its relevance reaches this share of the best evidence
  semantic_check: false         # ask models.verification to audit each answer before accepting it
  require_qualification: false  # cap results at `uncertain` unless this model + settings are qualified on your corpus
  # block: [quote_in_other_passage]     # extra finding categories that block
  # allow: [quote_approximate]          # categories that never block
  qualification: {min_tasks: 20, min_accepted_precision: 0.95, max_false_absence_rate: 0.02}

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

execution:                                      # how a run is carried out (never what it decides)
  max_concurrency: 1            # default width of every fan_out level (how much work is in flight); 1 = in order, no threads
  # fan_out: {tasks: 4, batches: 8, evidence_parts: 4, record_windows: 4, records: 8, retrieval: 1}   # per level; omitted = max_concurrency
  # resources: {llm_api: 8, rerank_gpu: 1}                      # calls each backend serves at once (capped by the model's own max_parallel)
  # assign: {extraction: llm_api, reranking: rerank_gpu}        # which resource each model uses (default: one per model)
  max_recovery_attempts: 2      # S12: recovery tries per task (0 = none)
  cache_model_calls: true       # a finished model call is not made again (resume after a crash)
recovery:                                       # S12: bounded second tries for tasks that were not accepted
  actions: [retry_call, widen_evidence, rediscover]   # measure before adding refield / deepen / escalate_model
  max_calls: 6
  max_seconds: 180
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
| `adapters/` | S03, S04, S06-S10 | Interfaces for parser / embedder / reranker / extractor / interpreter / verifier, the registry that builds them from the runtime config, no-op fakes, the embedding adapters `embedding_sentence_transformers.py` (any local model) and `embedding_openai.py` (any `/v1/embeddings` endpoint), and the reranker adapters `rerank_cross_encoder.py` (any CrossEncoder) and `rerank_http.py` (Cohere / Jina / vLLM `/rerank`), and the chat-model adapters `chat_ollama.py` (local Ollama, native schema-constrained output and context size) and `chat_openai.py` (any `/chat/completions` endpoint). **The only place model-specific code may live.** | New backend: adapter class + `register_adapter(...)` in `adapters/__init__.py` |
| `config/` | - | Runtime settings (`RuntimeConfig`): models, limits, parsing thresholds, storage. | `runtime.py` |
| `artifacts/` | - | SHA-256 fingerprints and the on-disk store (parsed documents, raw parser output, manifests, original files). Keys include document hash + settings, so a changed input or setting never hits stale cache. | `store.py` |
| `indexing/` | S04 | **`chunking_pipeline.py` is the whole chunking pipeline in one readable file** (numbered steps, each calling a helper). Helpers: `structure.py` heading hierarchy, `continuation.py` page-break joins, `units.py` / `search_text.py` search view with a map back to the source, `sizing.py` merge/cut rules, `passages.py`, `references.py`. `builder.py` = cache + indexes + coverage ledger. `report.py` = HTML view. | Chunking behaviour: `chunking_pipeline.py` and the helper it calls; sizes: runtime config |
| `indexing/stores/` | S04 | Pluggable databases: `base.py` interfaces (`LexicalIndex`, `VectorStore`), `sqlite_fts5.py` (BM25), `numpy_store.py` (exact cosine). | New database: subclass + register in `stores/__init__.py` |
| `planning/` | S05 | **`planning_pipeline.py` is the whole planning stage in one readable file**: after retrieval and before any model call, it measures how much evidence the value tasks share (`overlap.py`) and, with `planning.grouping: auto`, decides which tasks are answered by the same model call (`grouping.py`; `extraction/capacity.py` says what one call can hold). | Planning behaviour: `planning_pipeline.py` and `grouping.py`; limits: `planning:` in the runtime config |
| `retrieval/` | S06 | **`retrieval_pipeline.py` is the whole evidence-retrieval pipeline in one readable file** (queries -> candidates -> fusion -> rerank -> selection -> expansion -> packaging). Helpers: `queries.py`, `candidates.py`, `fusion.py`, `rerank.py`, `selection.py`, `definitions.py`, `expansion.py`, `packaging.py`; `report.py` prints a run's trace. | Retrieval behaviour: `retrieval_pipeline.py` and its helper; depth: `retrieval.profile(s)` in the runtime config |
| `extraction/` | S07 | **`value_pipeline.py` is the whole value-extraction pipeline in one readable file** (tag passages -> split by context -> ask the model -> read candidates and locate their quotes -> normalize -> derive -> reconcile -> conclude). Helpers: `prompts.py` (what the model is told), `schema.py` (JSON schema it must follow), `partition.py`, `reading.py`, `quotes.py`, `normalize.py` (dates, numbers, amounts by code), `derivation.py` (arithmetic by code, only when the task allows), `reconcile.py`, `choosing.py` (precedence rules), `calling.py`, `report.py` | Behaviour: `value_pipeline.py` and its helper; wording: `prompts.py`; model: `models.extraction` in the runtime config |
| `records/` | S09 | **`records_pipeline.py` is the whole records stage in one readable file** (scope -> discover boundaries window by window -> exact positions -> identity -> fields per record -> reconcile -> audit). Helpers: `scope.py`, `discovery.py`, `spans.py`, `fields.py`, `audit.py`, `outline.py`, `prompts.py`, `schema.py` | Behaviour: `records_pipeline.py` and its helper; boundaries: the YAML `record.identity` / `record.boundaries`; knobs: `records:` |
| `interpretation/` | S08 | **`interpretation_pipeline.py` is the whole interpretation stage in one readable file** (frame -> evidence -> ask -> read quotes -> check by code -> judge -> conclude). Helpers: `prompts.py`, `schema.py`, `reading.py`, `checks.py`, `judging.py`, `tools.py` (exact calculations the engine runs for the model; MCP-shaped definitions) | Behaviour: `interpretation_pipeline.py` and its helper; wording: `prompts.py`; model: `models.interpretation`; knobs: `interpretation:` |
| `validation/` | S10, S11 | **`validation_pipeline.py` is the whole validation and acceptance stage in one readable file** (structure -> grounding -> normalization -> consistency -> coverage / risk register -> optional semantic check -> qualification -> policy -> decision). Helpers: `structure.py`, `grounding.py`, `numeric.py`, `consistency.py`, `coverage_check.py`, `semantic.py`, `policy.py` (profiles), `qualification.py`, `findings.py` | Behaviour: `validation_pipeline.py` and the layer it calls; which findings block: `policy.py` or `acceptance:` in the runtime config |
| `execution/` | S05 | How a run is carried out: `call_cache.py` (a finished model call is never paid twice), `meter.py` (calls, tokens and seconds per stage and task), `scope.py` (the scope of one run), `scheduler.py` (independent tasks side by side, never more than the backend serves). | Limits: `execution:` in the runtime config |
| `recovery/` | S12 | **`recovery_pipeline.py` is the whole recovery stage in one readable file** (plan -> budget -> act -> keep -> record). Helpers: `actions.py` and `records_actions.py` (which finding calls for which change), `budget.py`, `keeping.py` (which result stands), `attempt.py`. | Which actions run: `recovery.actions`; a new action: `actions.py` |
| `review/`, `report/`, `api.py` | S13 | Reviews kept apart from predictions (`review/`), the self-contained HTML report (`report/`), and the public Python API `Extractor` (`api.py`). | `review/reviewing.py`, `report/html_report.py` |
| `pipeline/` | all | **`extraction_pipeline.py` is the whole engine in one readable file**: S01-S13 as numbered calls. `run_report` returns the stable `RunReport` (results + what the run cost). | `extraction_pipeline.py` |
| `evaluation/` | - | Loads and validates your labeled corpus (`corpus.py`), scores engine output (`harness.py`), measures parsing (`parsing_eval.py`), chunking (`indexing_eval.py`), retrieval (`retrieval_eval.py`) and value-extraction (`extraction_eval.py`) quality. | `corpus.py` for the label format |
| `events.py` | all | Activity log every stage writes to (`emit`, `step`, `progress`, `run_scope`); the UI Logs tab and progress strip read it. | New stage: log each numbered step |
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

### How execution and recovery work (S05, S12)

**Execution.** Every model call goes through one function (`extraction/calling.py::call_model`). It reads the answer from the
call cache when the same model was already asked the same prompt, counts calls, tokens and seconds per stage and task, and
retries a failed call *with a change that depends on why it failed* (a transport error waits and re-sends; bad JSON asks for
JSON only; a cut-off answer asks for a shorter one; a prompt that does not fit is not retried). Tasks run one dependency layer
at a time. A task whose `depends_on` was not accepted is not sent to the model.

**Parallelism** is two separate settings, so a laptop and a production deployment differ only in config:
`execution.fan_out` says how much *work* is in flight per level (tasks, retrieval, plan batches, evidence parts, discovery
windows, records; `max_concurrency` is the default of every level), and `execution.resources` / `assign` say how many *calls*
each backend serves at once. Every call to a model holds one slot of its resource, so on a laptop the embedder, reranker and
LLM share `gpu: 1` and take turns, while in production the LLM can sit on `llm_api: 8` and the reranker stay on `rerank_gpu: 1`.
A resource is capped by the model's own `max_parallel`. Width 1 runs a plain loop (no threads). Results do not depend on any of
this, and neither does the run id. `execution/concurrency.py` is standard-library only and can be copied into another project.

**Recovery.** Open `src/extractor/recovery/recovery_pipeline.py`. A task that was not accepted gets at most
`execution.max_recovery_attempts` targeted tries, within `recovery.max_calls` and `recovery.max_seconds`:

| Action | When | What changes | Default |
| --- | --- | --- | --- |
| `retry_call` | a model call failed | smaller calls (or no reasoning preamble when the answer was cut off) | on |
| `widen_evidence` | the evidence budget left a passage out | the evidence is gathered again with a bigger budget | on |
| `rediscover` | the records audit found a numbering gap, uncovered numbered text, a failed or cut-off window | only the named block regions are read again and merged | on |
| `refield` | a required field of a record was not accepted | that record's fields again, with candidate verification | off |
| `deepen` | candidates conflict, a quote is not in the source | the next retrieval and reading depth | off |
| `escalate_model` | the same | the same evidence, read by `models.escalation` | off |

Every attempt runs extraction **and** validation again, so a recovered answer passes the same policy as a first answer. The new
result stands only if it is better; two attempts that found different values stay `uncertain` and both values are shown; an
accepted answer is never replaced. Each attempt is on the result (`recovery`: action, reason, change, cost, kept or not). The
actions that are off change how an answer is produced: turn one on only after `eval --recovery` shows rescues with no harm.

**Prompt injection.** Every system prompt ends with the rule that document text is data, not instructions. The rule lowers
the risk; the engine is what bounds it: a model can only return JSON of a fixed shape, a value must be quoted from the source,
and no model output can change a status or a setting.

### Results, review and reports (S13)

```
python -m extractor run spec.yaml --docs contract.pdf --config runtime.yaml     # prints: run 3f9c1a2b7d4e5f60
python -m extractor runs                                                       # every saved run, newest first
python -m extractor review 3f9c1a2b7d4e5f60 maturity_date --correct 2027-04-01 --reviewer ana --note "amended in July"
python -m extractor review 3f9c1a2b7d4e5f60 clauses --record "(b)" --field text --accept
python -m extractor reviews 3f9c1a2b7d4e5f60                                   # the answers a person ends up with
python -m extractor report 3f9c1a2b7d4e5f60 --out report.html                  # one self-contained file, no scripts
python -m extractor labels 3f9c1a2b7d4e5f60 --out labels.yaml                  # reviewed corrections as corpus labels
```

```python
from extractor.api import Extractor

engine = Extractor("runtime.yaml")
run = engine.run("spec.yaml", ["contract.pdf"])               # RunReport: results + what the run cost, saved under run.run_id
engine.review(run.run_id, "maturity_date", "correct", value="2027-04-01", reviewer="ana")
for item in engine.reviewed(run.run_id):                       # engine answer, or the reviewer's, with the engine's always kept
    print(item.target.label(), item.source, item.status.value, item.value)
```

A review never changes the run: it is stored beside it with a snapshot of what the engine said, a later review supersedes an earlier one (the earlier stays on file),
and a review of an answer that has since changed is shown as stale and not applied. `accept` is only possible for an answer the engine stood behind (supported or not found).

**Where your documents go.** By default every model must run on this machine. A model at a non-local `base_url` (a hosted chat, embedding or rerank endpoint) is refused with
a message naming the capability and host, unless the runtime config sets `privacy: {allow_remote_models: true}`. Every result lists the capabilities that were remote
(`execution.remote`), and the report shows it at the top.

### How validation and acceptance work (S10, S11)

Open `src/extractor/validation/validation_pipeline.py`: `validate_and_accept` lists the steps in order. The input is the
S07 proposal; the output is the status of the task with plain-language reasons.

1. **Structure:** the value has the declared type, allowed label and cardinality.
2. **Source grounding:** the cited blocks exist, the quote was found in the evidence, the value is in the quote.
3. **Normalization:** the canonical value still means what the quote says: a dropped scale word ("NTD 12 million" read as
   12) or an amount written two ways that disagree ("1.2 million (SAY ONE MILLION AND TWENTY THOUSAND ONLY)") blocks.
4. **Consistency:** `depends_on` tasks were accepted, the value is not redacted, discarded candidates are listed.
5. **Coverage (the risk register):** for a "not found" answer the engine lists everything that could hide the value:
   unresolved parsing issues, passages left out for the budget, failed calls, unresolved references. A parsing issue is
   scored for relevance to THE TASK with the reranker applied only to the damaged passages: a damaged fee table blocks
   "not found" for a fee question and not for a name question, so cost follows the damage, never the document size, and
   no model tokens are spent. A relevant risk makes the task `uncertain` and records a recovery hint (reparse that
   page, inspect those passages) for the future recovery stage. Pages that stayed empty after the whole OCR retry
   ladder count as blank (information).
6. **Semantic support (optional):** `acceptance.semantic_check` asks `models.verification` (it may be a bigger model than the
   extractor) to audit the one answer about to be accepted. Its answer is a finding, never proof; a failed call is reported.
7. **Qualification (optional):** `eval --acceptance --qualify` writes a record when your corpus metrics reach
   `acceptance.qualification`; `acceptance.require_qualification` then caps unqualified model + settings at `uncertain`.
8. **Policy:** the profile decides which findings block. `strict`: any doubt (rejected candidates, quote in another passage,
   problems on the cited page, blank pages, missing attachments). `standard` (default): grounding, normalization,
   consistency, relevant coverage risks, semantic failures. `lenient`: standard minus approximate quotes and minus
   unresolved references inside the document. `acceptance.block` / `acceptance.allow` change single categories.
9. **Decision:** a found answer with nothing blocking is `supported`; with something blocking it is `uncertain` (the value
   goes to `alternatives`); an absent answer is the task's `on_absent` only if the inspection was complete, else `uncertain`.

A quote the model did not copy verbatim is not simply rejected: if the VALUE is in the cited passage its blocks are cited and
the finding `quote_relocated` is recorded (information for `standard`, blocking for `strict`).

Retrieval scores and model confidence are never used to accept: on the labeled corpus neither separated right answers from
wrong ones. `python -m extractor eval --acceptance` reports automation rate, accepted-answer precision, false accepts,
false absences and the blocking findings; `--save` / `--from` make tuning a profile a matter of seconds.

### How records extraction works (S09)

A records task asks for EVERY occurrence of something inside a bounded scope (all clauses, all milestones, all defined terms). Top-k retrieval cannot do that,
because a missed record is invisible, so the whole scope is read. Open `src/extractor/records/records_pipeline.py`: `extract_records` is seven numbered steps.

1. **Scope:** small documents are read entirely; for large ones a model names the region from the document outline and the engine checks it (fallback: the whole document, reported).
2. **Discover:** the scope is read window by window; the model lists each record as (key, start block, its first words, end block). It never returns the text.
3. **Positions:** the engine finds the first words inside the named block (several records can share a block); a record ends where the next one starts, even across a page.
4. **Identity:** the key as printed; the record id derives from the source position.
5. **Fields:** each record's own text is the ONLY evidence of its fields (one grouped call for the value fields, S08 for label fields); every field goes through S10/S11.
6. **Reconcile:** a record seen in two windows is one record; the same key in two places is a `duplicate_key`.
7. **Audit:** numbering gaps ((a) (b) (d)), numbered text of the same family that no record covers, failed or truncated windows, parsing problems in the scope. Only then is the collection `complete`.

`supported` needs completeness `checked` AND every required field of every record accepted; otherwise `uncertain`, and the records read so far stay in the result with a status per field.
Write `record.identity` and `record.boundaries` in the YAML as precisely as you can ("Start at each (i)-(xi) under 4.1; stop before the next same-level item; exclude printed page numbers"): the model reads them.
`eval --records --save DIR` measures it; `eval --attribute-records DIR` says why each missing, extra or wrong record happened. **To do:** nested records (`record.parent`).

### How interpretation works (S08)

An interpretation task decides a label under criteria ("may the CRO subcontract?" -> `permitted` / `conditional` / `prohibited`).
Open `src/extractor/interpretation/interpretation_pipeline.py`: `interpret_task` is seven numbered steps.

1. **Frame:** the question, every label with its meaning and the task's `decision_criteria`.
2. **Evidence:** the S06 package, cut to what fits ONE call (a decision weighs all the evidence together); passages left out become gaps, never silence.
3. **Ask:** the model lists, for each label, the quotes that support it (`supports`, or `supersedes` for an amendment), then the label (or `none`),
   a two-sentence justification, the conditions that attach and what it could not settle. Exceptions and conditions must be listed even when they point at another label.
   If the decision needs a calculation, the model asks for it (`tool_calls`), the engine runs it exactly and the model answers again with the result.
4. **Read:** every quote is searched in the evidence by code; a quote that is not there never counts.
5. **Check:** the chosen label needs a located quote; located quotes for other labels are "competing evidence".
6. **Judge:** one call that can only downgrade the label to `uncertain` (an exception ignored, a request read as an approval...). Turn it off with `interpretation.judge: false`.
7. **Conclude:** `found` | `absent` (the model found no decision and nothing was missed) | `uncertain` (conflict, unbacked label, judge disagrees, evidence incomplete) | `failed`.

The proposal then goes through S10/S11 exactly like a value. `TaskResult.interpretation` keeps every quote by label, the justification and the tool results.

```yaml
models:
  interpretation: {name: ollama_chat, params: {model: "qwen3:14b", context_tokens: 16384}}   # optional: while `fake`, the extraction model is used
interpretation:
  judge: true            # downgrade-only second look
  think: true            # reason before answering (default: +3 correct of 30 on the corpus, 2x the time); `light` profile = false
  tools: [calculate, compare_numbers]
```

**To do (not forgotten):** generated `text` answers (summaries grounded in evidence) for interpretation tasks; the corpus has none yet. **Tools:** `tools.py` registers
in-process tools whose definitions have the MCP shape, so tools served by an MCP server can be added later with a small adapter (not built yet).

**Finding out why answers are wrong:** `eval --extraction --kinds interpretation --save DIR` stores every package, proposal and the labeled quotes;
`eval --attribute DIR` (also shown in the Corpus tab) puts each miss in one cause (parse, retrieval, evidence, judgement, false decision, failure) and says what can be fixed.

### How value extraction works (S07)

Open `src/extractor/extraction/value_pipeline.py`: `extract_value` is `ask_task` (steps 1-4) then `conclude_task` (steps 5-10).

1. **Tag:** the evidence passages get short tags (P1, P2...) that the model can copy reliably.
2. **Split:** if the evidence does not fit the model's context (`context_tokens` of the adapter), it is
   cut into several calls in source order; nothing is dropped.
3. **Ask:** the model must list EVERY value the passages state (not choose one), each with the passage tag,
   a verbatim quote, the value as written, whom/when it is about and its conditions. The answer is forced
   into a JSON schema built from the task (a label task can only answer an allowed label).
4. **Read:** the engine searches each quote in the evidence and maps it to source blocks (page, box).
   A quote that is not in the evidence is flagged `quote_not_located`, never trusted.
5. **Normalize:** dates become ISO, numbers/amounts canonical, by code ("thirty (30) days" -> 30,
   "27th day of March, 2017" -> 2017-03-27, "NTD 12 million" -> 12000000). Unreadable or ambiguous values
   (`03/04/2027`) stay as written with a `not_normalized` flag; numeric dates are settled only when the
   document's own dates prove its convention (`1/14/02` proves month-first).
6. **Screen (code gates):** a candidate is rejected (flag `rejected:<why>`, still visible) when it is redacted
   (`[***]` -> the task is `uncertain`), its value is not in its own quote, it is only a field label or blank
   (`Contract No:` -> absence), or a typed answer cannot be read as that type (`uncertain`).
7. **Derive:** only if the task has `rules.derivation`; the model names an operation over extracted
   candidates, the engine computes it exactly and marks the result `derived`.
8. **Verify:** one extra model call per task asks, for every candidate, "does this quote state THE value the
   field asks for?" It can only remove candidates (a different date, party or period; a label; a formula).
9. **Reconcile:** candidates that agree merge (spelling, case, hyphenation and a leading "the" do not make a
   different value); different values are a conflict, decided only by the task's `precedence` rules (the
   model applies them; it cannot invent one). No rule, or rules that do not decide: `uncertain` with all
   candidates kept.
10. **Conclude:** `found`, `absent` (nothing stated AND every call worked AND retrieval left no gap),
   `uncertain` (conflict, or "absent" that cannot be trusted) or `failed` (model/transport problem).

The result is a **proposal**. Until validation and acceptance exist (phase 6) a found value is returned as
`uncertain` with the value under `alternatives`; absence follows the task's `on_absent`.
`python -m extractor extract spec.yaml <task> --docs ...` prints candidates, steps and every model call
(the full prompts and raw answers are in the json it writes).

**Quality knobs** (`extraction:` in the runtime config, all measurable with `eval --extraction`):
`screen_candidates`, `verify_candidates`, `passages_per_call` (1 = one focused call per passage),
`think_extraction` / `think_verification` / `think_choice` (let the model reason first: slower, more accurate).

**Shared calls** (`planning:` in the runtime config, off by default): `grouping: auto` answers tasks that share most of their
evidence with ONE model call (fewer calls, less repeated reading). Each task still reads only its own evidence, is verified
and validated on its own, and is asked again alone whenever the shared answer cannot be trusted (call failed, field missing,
nothing found, a value taken from another task's passage). Limits are plain numbers (`max_fields_per_call`,
`max_foreign_share`, `output_tokens_per_field`, `reask_absent`), so a bigger model only needs different numbers.
Measure it before adopting it: `python -m extractor eval --extraction --grouping off,auto` (or the Corpus tab's
"compare grouping") runs the same evidence both ways and lists every task whose result changed.

**Which model:** `models.extraction`. Local: `ollama_chat` (`ollama pull qwen3:8b`; it sends the context
size to Ollama, whose default of 4096 tokens would silently cut evidence). Hosted or other servers:
`openai_compatible_chat` with `base_url`, `model`, `api_key_env`. Both force the JSON schema. Moving to a
bigger model is a config change only.

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
- `python -m extractor eval --extraction [--profile light,standard,deep]` runs retrieval and S07 on every
  labeled value task and reports, per profile: correct, wrong (and wrong-but-cited: the labeled quote was in
  the passage the value came from), abstained (`uncertain`), **false absence** (said absent, the label has a
  value), **false value** (invented one where the label says `not_found`), failed, seconds per task.
- `python -m extractor eval --acceptance [--save DIR | --from DIR] [--qualify]` runs the labeled value tasks through S06, S07 and
  S10/S11 and reports, per profile: automation rate (share `supported`), **accepted-answer precision**, false accepts,
  false absences, over-abstention (the proposal was right but acceptance blocked it) and the blocking findings.
- `python -m extractor eval --recovery [--cases ...] [--tags ...] [--save DIR]` runs the WHOLE engine and reports, per recovery
  action: attempts, kept, **rescues** (not accepted before, right now), **harm** (a new false accept or false absence), calls and
  calls per rescue, with automation and precision before and after.
- `--repeat N` (with `--acceptance` or `--records`) runs N times without the call cache and prints the spread of each metric:
  a change smaller than the spread is noise. `--no-cache` makes every call again. Acceptance and records measures print their
  numbers per `split:` tag: design fixes on train / validation cases and report the test split.
- `python -m extractor eval --score` scores full engine output (status and value accuracy).

## 6. Rules of the codebase

- Adding a model or library = a new adapter/backend class + registration. Nothing else changes.
- The YAML says *what*; `RuntimeConfig` says *which model / limits*. Never mix them.
- Heavy libraries are imported only inside `parsing/backends/*`, the embedding adapters and the store modules, and load lazily.
- Every pipeline is ONE readable `*_pipeline.py` file with numbered steps calling helpers in their own
  modules (`chunking_pipeline.py`, `retrieval_pipeline.py`, `value_pipeline.py`, `interpretation_pipeline.py`, `records_pipeline.py`, `validation_pipeline.py`, `recovery_pipeline.py`, and the whole engine in `extraction_pipeline.py`).
  Models, databases and depth are never hard-coded in them.
- Everything crossing a stage boundary is a type from `contracts/`.
- Every generative call goes through `extraction/calling.py::call_model` (cache, meter, retries, the document-is-data guard). Never call `adapter.generate` from a stage.
- Update ARCHITECTURE.md when a contract changes.
