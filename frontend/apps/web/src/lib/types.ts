// Shapes of the engine's output (`extractor.run/1` RunReport as served by /api/run) and of the UI's own view of it.

export type Status = "supported" | "not_found" | "uncertain" | "failed"
export type Kind = "value" | "interpretation" | "records"

export interface SourceRef {
  document_id: string
  block_id: string
}

export interface Finding {
  layer: string
  category: string
  message: string
  severity: "info" | "material"
}

export interface Stance {
  label: string
  role: string
  quote: string | null
  evidence: SourceRef[]
}

export interface FieldResult {
  field_id: string
  status: Status
  value: unknown
  evidence: SourceRef[]
}

export interface EngineRecord {
  record_id: string
  key: string
  text: string
  block_ids: string[]
  fields: FieldResult[]
}

export interface TaskResult {
  task_id: string
  kind: Kind
  status: Status
  value: unknown
  records: EngineRecord[]
  completeness: string | null
  evidence: { ref: SourceRef; role: string; quote: string | null }[]
  validation: { findings: Finding[] }
  acceptance: { policy: string; reasons: string[]; blocking: Finding[] } | null
  alternatives: unknown[]
  scope_note: string
  interpretation: {
    label: string | null
    justification: string
    conditions: string[]
    unresolved: string[]
    stances: Stance[]
  } | null
  diagnostics: string[]
}

/** What a person ends up with for one target: the engine's answer, or the reviewer's. The engine's answer is always kept next to it. */
export interface ReviewedItem {
  target: { task_id: string; record_key: string | null; field_id: string | null }
  source: "engine" | "human"
  status: Status
  value: unknown
  decision: "accept" | "correct" | "reject" | null
  review_id: string | null
  stale: boolean
  note: string
  engine_status: Status
  engine_value: unknown
}

export interface RunOutput {
  run_id?: string
  /** the engine's own run id (S13): the run is saved under it and reviews attach to it; absent in runs saved before it existed */
  engine_run_id?: string
  results: TaskResult[]
  summary?: {
    seconds: number
    statuses: Record<string, number>
    total: { calls: number; cached: number; prompt_tokens: number; completion_tokens: number }
  }
  recorded?: { at: string; seconds: number; model: string }
  /** kept with a saved run: the spec as it was, and where every field went in the pipeline (sanitized trace events) */
  spec_text?: string
  trace?: EngineEvent[]
}

/** One saved job of the engine's history (kept on disk by the engine, so it survives restarts). */
export interface HistoryRow {
  run_id: string
  kind: string
  label: string
  status: "ok" | "error"
  started_at: string
  seconds: number | null
  subject: string
  headline: string
  error: string | null
  params: { spec_name?: string; documents?: string[] }
  result?: RunOutput
}

export interface UploadedDocument {
  name: string
  size: number
  parseable: boolean
  parsed?: boolean
  indexed?: boolean
}

export interface PageInfo {
  page: number
  width: number
  height: number
}
export interface BlockInfo {
  id: string
  page: number
  bbox: [number, number, number, number]
  text: string
}
export interface BlocksData {
  pages: PageInfo[]
  blocks: BlockInfo[]
}

export interface EngineEvent {
  seq: number
  time: number
  level: "debug" | "info" | "ok" | "warn" | "error"
  stage: string
  message: string
  run: string
  kind: string
}

// --- the UI's view ---------------------------------------------------------

export interface Highlight {
  blockId: string
  page: number
  bbox: [number, number, number, number]
}

export interface Quote {
  text: string
  role?: string
  label?: string
  highlights: Highlight[]
}

export interface ReviewItem {
  id: string
  label: string
  kind: Kind
  status: Status
  /** short text shown in the list; null when there is no value */
  display: string | null
  quotes: Quote[]
  findings: Finding[]
  reasons: string[]
  conditions: string[]
  justification: string
  scopeNote: string
  records: { key: string; text: string; fields: { name: string; value: string }[]; highlights: Highlight[] }[]
  /** every located block this result rests on */
  highlights: Highlight[]
  /** first page with a reliable source block, if any */
  page: number | null
}

export type Source =
  | { kind: "sample"; name: string; pdfUrl: string; yaml: string }
  | { kind: "upload"; name: string; pdfUrl: string; yaml: string; /** shown next to the name for a run opened from the history */ meta?: string }

/** What the spec builder returns: the YAML, whether it compiles, and what to double-check. */
export interface SpecDraft {
  yaml: string
  valid: boolean
  errors: { code: string; path: string; message: string }[]
  warnings: string[]
  calls: number
  seconds: number
}
