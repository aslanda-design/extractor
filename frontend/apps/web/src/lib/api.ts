import type { BlocksData, EngineEvent, HistoryRow, ReviewedItem, RunOutput, SpecDraft, UploadedDocument } from "./types"

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`
    try {
      msg = ((await res.json()) as { error?: string }).error ?? msg
    } catch {
      /* not JSON */
    }
    throw new Error(msg)
  }
  return res.json() as Promise<T>
}

export const api = {
  documents: () => fetch("/api/documents").then((r) => json<UploadedDocument[]>(r)),
  /** Saved extraction runs, newest first (the engine keeps them as files in its workspace). */
  runs: () => fetch("/api/history?kind=run").then((r) => json<HistoryRow[]>(r)),
  runDetail: (id: string) => fetch(`/api/history/${encodeURIComponent(id)}`).then((r) => json<HistoryRow>(r)),
  deleteRun: (id: string) => fetch(`/api/history/${encodeURIComponent(id)}`, { method: "DELETE" }).then((r) => json<{ ok: boolean }>(r)),
  upload: (file: File) => {
    const body = new FormData()
    body.append("files", file)
    return fetch("/api/documents", { method: "POST", body }).then((r) => json<string[]>(r))
  },
  specs: () => fetch("/api/specs").then((r) => json<string[]>(r)),
  spec: (name: string) => fetch(`/api/specs/${encodeURIComponent(name)}`).then((r) => json<{ name: string; text: string }>(r)),
  saveSpec: (name: string, text: string) =>
    fetch("/api/specs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, text }) }).then((r) =>
      json<{ name: string }>(r)
    ),
  validateSpec: (text: string) =>
    fetch("/api/specs/validate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) }).then((r) =>
      json<{ ok: boolean; errors?: { message: string; path: string }[] }>(r)
    ),
  /** Writes the YAML from a plain-language request; `current` makes it an edit of that YAML. */
  draftSpec: (request: string, current?: string) =>
    fetch("/api/specs/draft", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ request, current }) }).then((r) =>
      json<SpecDraft>(r)
    ),
  blocks: (name: string) => fetch(`/api/documents/${encodeURIComponent(name)}/blocks`).then((r) => json<BlocksData>(r)),
  run: (spec: string, documents: string[]) =>
    fetch("/api/run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ spec, documents }) }).then((r) =>
      json<RunOutput>(r)
    ),
  /** Reviews of a saved engine run and the answers a person ends up with. */
  reviews: (engineRunId: string) =>
    fetch(`/api/engine-runs/${encodeURIComponent(engineRunId)}/reviews`).then((r) => json<{ items: ReviewedItem[] }>(r)),
  review: (engineRunId: string, body: { task_id: string; decision: string; value?: unknown; note?: string; reviewer?: string }) =>
    fetch(`/api/engine-runs/${encodeURIComponent(engineRunId)}/reviews`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => json<{ review_id: string }>(r)),
  reportUrl: (engineRunId: string) => `/api/engine-runs/${encodeURIComponent(engineRunId)}/report`,
  /** Starts a run in the background and answers at once: it is on record (running) from this moment, poll `runDetail(run_id)`. */
  startRun: (spec: string, documents: string[]) =>
    fetch("/api/run/start", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ spec, documents }) }).then((r) =>
      json<{ run_id: string; status: string; engine_run_id: string }>(r)
    ),
  fileUrl: (name: string) => `/api/documents/${encodeURIComponent(name)}/file`,
  lastLogSeq: () => fetch("/api/logs?since=2147483647").then((r) => json<{ last: number }>(r)).then((d) => d.last),
}

/** Opens the engine's event stream from `since`. Returns a function that closes it. */
export function streamLogs(since: number, onEvent: (e: EngineEvent) => void, onError?: () => void): () => void {
  const es = new EventSource(`/api/logs/stream?since=${since}`)
  es.onmessage = (m) => onEvent(JSON.parse(m.data) as EngineEvent)
  es.onerror = () => onError?.()
  return () => es.close()
}

export async function loadSample() {
  const get = async <T,>(file: string, optional = false): Promise<T | null> => {
    const res = await fetch(`/sample/${file}`)
    if (!res.ok || res.headers.get("content-type")?.includes("text/html")) {
      if (optional) return null
      throw new Error(`sample file ${file} is missing`)
    }
    return (file.endsWith(".yaml") ? await res.text() : await res.json()) as T
  }
  const [result, blocks, yaml, logs] = await Promise.all([
    get<RunOutput>("result.json"),
    get<BlocksData>("blocks.json"),
    get<string>("spec.yaml"),
    get<EngineEvent[]>("logs.json", true),
  ])
  return { result: result!, blocks: blocks!, yaml: yaml!, logs }
}
