# Extractor review frontend

React + Vite + shadcn/ui (Base UI, Nova) app for reviewing extraction results next to the source PDF.
It calls the Python engine API in `../ui` (FastAPI) through the Vite proxy (`/api`).

## Run everything locally

```
cd frontend
npm install
npm run dev
```

This starts the engine API (`python ui/run.py --echo-events`, port 8765) and the web app (http://localhost:5173) together.
Every engine event is printed in this terminal with an `[engine]` prefix and shown, live, in the Engine logs panel.
Needs the repo venv (`.venv`), Java/CUDA as described in the root README, and Ollama with the model set in the workspace settings.

Other scripts: `npm run build`, `npm run lint`, `npm run typecheck`, `npm run dev:web` (web app only).

## Layout

- `apps/web/src/App.tsx`: the review screen (60% PDF / 40% results on desktop, tabs on mobile), top bar, run flow.
- `components/pdf-viewer.tsx`: pdf.js pages, page navigation, zoom, amber evidence boxes.
- `components/results-panel.tsx`: result list, status, source quote, validation details.
- `components/logs-panel.tsx`: collapsible Engine logs (live via SSE, or "Recorded" for the sample).
- `lib/review.ts`: turns engine results into what the screen shows; coordinates are only used when the cited block is known.
- `apps/web/public/sample/`: the fictional service agreement and the saved output of a real engine run on it
  (`scripts/build_sample.py` regenerates it).

## Dark mode and the Test engine route

- **Theme**: the top bar button cycles light, dark and system. The choice is kept in `localStorage` and applied before first paint (`apps/web/index.html`). Colors are tokens in `packages/ui/src/styles/globals.css` (`.dark` overrides); status and log colors use `ok`, `warn` and `bad` tokens.
- **Test engine** (`#/dev`, button in the review top bar): a developer bench in `apps/web/src/dev/`. Tabs: **Logs** (every engine event, live, filter by level, stage, run and text; pause, clear), **Run** (validate a spec, run it on uploaded documents, cost summary, result tree, logs of that run only), **Corpus** (all corpus measures with split, kind and case filters; the acceptance, records and recovery measures show their KPIs as tiles and tables: automation, accepted precision, false accepts and false absences, record recall and precision, field accuracy, rescues and harm per recovery action, each also per train / validation / test split, plus the list of wrong tasks; `src/dev/corpus-kpis.tsx`; the other measures show a metric table; every measure keeps its full result), **History** (every job the engine ran, with parameters, models and result), **Settings** (runtime config as JSON, reset, registered adapters). It only calls the existing `ui/server.py` endpoints.

## Live pipeline graph (`#/dev` > Pipeline)

An animated diagram of the engine: every field is a particle that travels Scope, Retrieve, Read, Validate (seven rings, one per validation layer), Decide and then lands in Supported, Not found, Uncertain or Failed. A field the engine does not accept goes down to Recover and loops back to Read and Validate again, so you can watch recovery rescue (or fail to rescue) it. Click a particle or a row to follow one field: its timeline, layer results and recovery passes.

Sources, always labeled: **Run on the engine** (live, from the same event stream as the `[engine]` terminal lines), **Replay last run** (the real events of the last live run, long waits compressed) and **Simulated demo** (a scripted run with a recovery loop, not engine output). Needs an engine that emits `trace` events (restart `npm run dev` after updating). Code: `src/pipeline/model.ts` (events to state), `pipeline-graph.tsx` (SVG and motion), `demo.ts`.

## Pipeline in the review screen, and the history

- While an extraction runs, the right-hand panel shows the **live pipeline graph** instead of a loading message (same style as the Results panel, following the app theme). When the run finishes it switches to the results; the **Results / Pipeline** switch in the panel header goes back to the graph, and **Replay** plays the run again.
- **History** (top bar) opens a drawer with every saved **run** and every **uploaded document**. Opening a run restores its document, answers, the spec as it was and the pipeline (replayable); opening a document shows it ready to run again, and **New extraction** can reuse any uploaded document and start from a past run's spec. Delete asks twice.
- Persistence: the engine keeps each run as a JSON file in `.extractor_artifacts/workspace/history/` and each upload in `.../uploads/`, the same files the old UI uses, so nothing is lost when the app closes. A saved run also stores `spec_text` and `trace` (the sanitized pipeline events).
