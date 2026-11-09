# Extractor UI

A local web interface for the engine. It lives in this separate folder and uses the engine
only through its public modules (`extractor.*`); the engine does not know about it.

## Launch

```
.venv\Scripts\python -m pip install -r ui\requirements.txt   # once
ui\launch.bat                                               # or: .venv\Scripts\python ui\run.py
```

Opens http://127.0.0.1:8765 in your browser. Options: `--port 8765`, `--workspace <folder>`,
`--no-browser`. It listens on localhost only; do not expose it without adding authentication.

## Tabs

| Tab | What you do |
| --- | --- |
| **Documents** | Upload PDFs/images (drag and drop). **Parse**: per-page routing (native vs OCR), per-page layout coverage (share of the PDF text layer the parser kept) and words restored, issues, and the interactive report (page image beside parsed blocks, boxes overlaid). **Chunk & index**: passages with their sections, token sizes, page-break joins, references, and a page view with every passage's boundary drawn on the original page; a search box queries the lexical and vector indexes side by side |
| **Extraction** | Ask one question about the selected documents (or load a value task from a spec): answer type, allowed answers, precedence / derivation rules, depth profile. Shows the outcome and value, every candidate the model reported with its quote located in the evidence, the engine's steps (normalizing, merging, applying precedence rules), the evidence handed to the model, and each model call (extract, verify, choose) with the exact prompt, JSON schema and raw answer. Candidates removed by a code gate or by verification stay listed with a red `rejected:` flag |
| **Specification** | Edit the extraction YAML with live validation: errors with their YAML path, task table, parallel execution layers, review flags. Save under a name |
| **Run** | Pick a spec and documents and run the pipeline. Value tasks run retrieval and extraction (a found value shows as a proposal under `uncertain`: nothing is accepted before phase 6); interpretation and records tasks return `failed` by design |
| **Settings** | Edit parsing thresholds, parser choice, OCR device and languages, plus the chunking rules, the embedding model (adapter + params) the lexical / vector databases, the extraction model (adapter + params) and the extraction behaviour settings. Saved to the workspace; re-parse / re-chunk to apply |
| **Corpus** | Validate `evaluation/corpus/` and measure parsing quality (quote recall, page accuracy, unresolved issues) chunking quality (share of labeled quotes that sit inside one passage) and value extraction (correct / wrong / false absence / false value, per profile). Every finished measure is saved in the run history, so after a reload the Corpus tab shows the latest result and a table of earlier measures to view. Tick which field types to score (types without an extractor yet are greyed out and become selectable as their phase lands) and optionally restrict to the train / validation / test cases or to the extraction projects (spec + documents + labels) you pick from the list |
| **History** | Every job you ran (parse, chunk and index, index search, retrieve, extract, run, corpus measures) is kept on disk, across reloads and restarts, with its parameters, the models and retrieval profile used, duration and result (failed jobs too). Filter by type, status or text; pick a run in the list or step through them with Newer / Older (or the arrow keys); run, search and corpus results are shown in place, parse / chunk / retrieve / extract results reopen in their own tab. Delete one run or everything shown. Input errors rejected before a job starts are not recorded |
| **Logs** | Live engine activity. Every job (parse, index, search, retrieve, extract, run, corpus measures) is a card with its stages (S03 parse, S04 index, S06 retrieve, S07 extract...), per-step timings, progress counters, cache hits, warnings and errors. A strip under the header shows the running job and its progress from any tab. Filter to warnings, clear, follow the newest line |

The first OCR parse downloads models and can take a couple of minutes; later ones reuse
them. Only one parse runs at a time (the GPU is shared).

## Files

| File | Purpose |
| --- | --- |
| `run.py` | Entry point: starts the server and opens the browser |
| `server.py` | FastAPI routes only (HTTP in, HTTP out) |
| `history.py` | `RunHistory`: one JSON file per run in `<workspace>/history/`; the one-line subject and headline of each kind of run |
| `service.py` | All logic (`Workspace`): uploads, parsing, spec validation, run, settings, corpus. No web-framework code, so it is easy to read and test |
| `static/index.html` | The whole front end (HTML + CSS + vanilla JS, no build step) |
| `launch.bat` | Double-click launcher using the project's `.venv` |
| `requirements.txt` | UI-only dependencies (FastAPI, uvicorn, python-multipart) |

Workspace (default `.extractor_artifacts/workspace/`): `uploads/`, `specs/`, `reports/`,
`runtime.yaml`. Caches and OCR models are shared with the command line.
