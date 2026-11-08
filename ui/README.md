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
| **Specification** | Edit the extraction YAML with live validation: errors with their YAML path, task table, parallel execution layers, review flags. Save under a name |
| **Run** | Pick a spec and documents and run the pipeline. Extraction stages are not built yet, so tasks return `failed` by design; it already shows scope, precedence and result format |
| **Settings** | Edit parsing thresholds, parser choice, OCR device and languages, plus the chunking rules, the embedding model (adapter + params) and the lexical / vector databases. Saved to the workspace; re-parse / re-chunk to apply |
| **Corpus** | Validate `evaluation/corpus/` and measure parsing quality (quote recall, page accuracy, unresolved issues) and chunking quality (share of labeled quotes that sit inside one passage) |

The first OCR parse downloads models and can take a couple of minutes; later ones reuse
them. Only one parse runs at a time (the GPU is shared).

## Files

| File | Purpose |
| --- | --- |
| `run.py` | Entry point: starts the server and opens the browser |
| `server.py` | FastAPI routes only (HTTP in, HTTP out) |
| `service.py` | All logic (`Workspace`): uploads, parsing, spec validation, run, settings, corpus. No web-framework code, so it is easy to read and test |
| `static/index.html` | The whole front end (HTML + CSS + vanilla JS, no build step) |
| `launch.bat` | Double-click launcher using the project's `.venv` |
| `requirements.txt` | UI-only dependencies (FastAPI, uvicorn, python-multipart) |

Workspace (default `.extractor_artifacts/workspace/`): `uploads/`, `specs/`, `reports/`,
`runtime.yaml`. Caches and OCR models are shared with the command line.
