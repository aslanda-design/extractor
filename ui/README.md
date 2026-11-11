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
| **Extraction** | Ask one question about the selected documents (or load a value task from a spec): answer type, allowed answers, precedence / derivation rules, depth profile. Shows the outcome and value, every candidate the model reported with its quote located in the evidence, the engine's steps (normalizing, merging, applying precedence rules), the evidence handed to the model, and each model call (extract, verify, choose) with the exact prompt, JSON schema and raw answer. Candidates removed by a code gate or by verification stay listed with a red `rejected:` flag. A **Validation and decision** card shows the engine's status (supported / not_found / uncertain / failed), its reasons and every finding by layer with whether it blocks, plus what recovery could do about it |
| **Specification** | Edit the extraction YAML with live validation: errors with their YAML path, task table, parallel execution layers, review flags. Save under a name |
| **Run** | Pick a spec and documents and run the pipeline (or press **Show the plan (S05)**: retrieval only, no chat model, shows which value tasks would share a model call and how much evidence they share; the Grouping select previews `off` / `auto` without saving it). Value tasks run retrieval and extraction (a found value shows as a proposal under `uncertain`: nothing is accepted before phase 6); interpretation and records tasks return `failed` by design |
| **Settings** | Edit parsing thresholds, parser choice, OCR device and languages, plus the chunking rules, the embedding model (adapter + params) the lexical / vector databases, the extraction model (adapter + params), the extraction behaviour settings, the verification model and the acceptance profile / options. Saved to the workspace; re-parse / re-chunk to apply |
| **Corpus** | Validate `evaluation/corpus/` and measure parsing quality (quote recall, page accuracy, unresolved issues) the evidence overlap between the tasks of each case (retrieval only); tick **compare grouping** to run the extraction measure twice on the same evidence (one call per task vs shared calls) and list the tasks whose result changed chunking quality (share of labeled quotes that sit inside one passage) value extraction (correct / wrong / false absence / false value, per profile) and **acceptance** (automation rate, accepted-answer precision, false accepts, false absences, blocking findings). Every finished measure is saved in the run history, so after a reload the Corpus tab shows the latest result and a table of earlier measures to view. Tick which field types to score (types without an extractor yet are greyed out and become selectable as their phase lands) and optionally restrict to the train / validation / test cases or to the extraction projects (spec + documents + labels) you pick from the list |
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

Phase 7 (interpretation): the Extraction tab has a Kind selector (value / interpretation) and decision criteria; the result shows the S08 decision card; Settings has the interpretation model and knobs; the Corpus extraction measure includes interpretation tasks and shows why each miss happens (error attribution).

Phase 9 (execution and recovery): the Run tab shows what the run cost (model calls, cached calls, tokens, seconds, by stage and task) and, for every task recovery touched, its trail (action, reason, change, before -> after, kept or not, cost); Settings has `execution`, `recovery`, `records` and the escalation model; the Corpus tab has "Measure recovery" (rescues, harm, calls per action); the activity strip shows S12; every job reads finished model calls from the call cache.

Phase 8 (records): the Run tab shows each records task with its audit, scope and record table; the Corpus tab has "Measure records" (recall, precision, field accuracy, completeness, causes).

React review frontend (`frontend/`): a separate app that talks to this server. It adds `GET /api/documents/{name}/file` (the original PDF for the viewer), `GET /api/documents/{name}/blocks` (page sizes and located blocks, parsing first if needed), and `GET /api/logs/stream` (server-sent events of engine events, sanitized by `service.public_event`: no secrets, long text cut, numbers only in `data`). `python ui/run.py --echo-events` also prints those same events to the terminal. See `frontend/README.md`; start both with `npm run dev` in `frontend/`.

Spec assistant: `POST /api/specs/draft` (`{request, current?}`) writes a specification from a plain-language request with the extraction chat model (one call, a repair call only if the draft does not compile) and returns `{yaml, valid, errors, warnings, calls, seconds}`. The React app's New extraction dialog uses it ("Describe it" box above the YAML). Only the React frontend has this screen; the old `ui/static/index.html` was not extended.

Live pipeline graph (`frontend/`, `#/dev` > Pipeline): the engine now emits `trace` events (`extractor.events.trace`) that place each field in the pipeline (declared, retrieve, read, validate with its layers, decide, recover loops, result). They travel on the same `/api/logs/stream` and the `--echo-events` terminal echo; `public_event` passes only short identifier strings for them.

History in the React frontend: `GET /api/history?kind=run` lists saved runs, `GET /api/history/{id}` returns one with its result. A `run` result now also carries `spec_text` (the YAML as it was, since `review.yaml` is rewritten on every extraction) and `trace` (sanitized pipeline events), so the frontend can reopen a past run and replay its pipeline.
