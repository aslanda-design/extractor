// Thin helpers for the engine's developer endpoints (ui/server.py). Everything here is plain JSON over HTTP.
import type { EngineEvent } from "@/lib/types"

export interface DevEvent extends EngineEvent {
  data?: Record<string, number | boolean | string>
}

async function call<T>(method: string, url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`
    try {
      msg = ((await res.json()) as { error?: string }).error ?? msg
    } catch {
      /* not JSON */
    }
    throw new Error(msg)
  }
  return (await res.json()) as T
}

export const get = <T,>(url: string) => call<T>("GET", url)
export const post = <T,>(url: string, body: unknown = {}) => call<T>("POST", url, body)
export const put = <T,>(url: string, body: unknown) => call<T>("PUT", url, body)
export const del = <T,>(url: string) => call<T>("DELETE", url)

export interface CorpusStatus {
  ok: boolean
  problems?: string[]
  cases?: string[]
  case_info?: { id: string; documents: string[]; spec: string; tags: string[] }[]
  path?: string
  tags?: string[]
  kinds?: { kind: string; labeled: number; implemented: boolean }[]
}

export interface HistoryRow {
  run_id: string
  kind: string
  label: string
  status: "ok" | "error"
  started_at: string
  seconds: number
  subject: string
  headline: string
  error: string | null
  settings: Record<string, string>
  params: unknown
  result?: unknown
}

/** Engine events from `since` (0 = everything the server still holds), then live. Returns a closer. */
export function openEventStream(since: number, onEvent: (e: DevEvent) => void, onState: (connected: boolean) => void): () => void {
  const es = new EventSource(`/api/logs/stream?since=${since}`)
  es.onopen = () => onState(true)
  es.onmessage = (m) => onEvent(JSON.parse(m.data) as DevEvent)
  es.onerror = () => onState(false)
  return () => es.close()
}
