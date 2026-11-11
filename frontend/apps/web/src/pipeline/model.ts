// The live pipeline model: engine events in, "where is each field now" out.
// Only `kind: "trace"` events (engine/events.py::trace) place a field; every other event just keeps its stage lit.
import type { EngineEvent, Status } from "@/lib/types"

export type NodeId = "scope" | "retrieve" | "read" | "validate" | "decide" | "recover" | "result"
export type TaskKind = "value" | "interpretation" | "records"

export const LAYERS = ["structure", "source grounding", "normalization", "consistency", "coverage", "semantic support", "qualification"] as const

export interface Visit {
  node: NodeId
  at: number
  text: string
  tone: "info" | "ok" | "warn" | "error"
}

export interface FieldState {
  id: string
  kind: TaskKind
  after: string[]
  node: NodeId
  /** the node the field came from (drives the route it travels) */
  from: NodeId | null
  /** bumps on every move so the graph can restart a route */
  moves: number
  layers: Record<string, number>
  loops: number
  action: string | null
  outcome: Status | null
  /** result of the last decision, before recovery: tells why the field went to recovery */
  decided: Status | null
  visits: Visit[]
}

export interface PipelineState {
  fields: Record<string, FieldState>
  order: string[]
  /** last time each stage code (S01..S13, run) emitted anything */
  stageAt: Record<string, number>
  running: boolean
  startedAt: number | null
  endedAt: number | null
  /** the last few readable lines, for the ticker */
  ticker: { seq: number; level: string; stage: string; message: string }[]
  /** the layer results of the field touched last (shown on the validate node when none is selected) */
  lastLayers: Record<string, number>
  /** activity per edge key "a>b" (a timestamp) */
  traffic: Record<string, number>
  events: number
}

export const initialState = (): PipelineState => ({
  fields: {},
  order: [],
  stageAt: {},
  running: false,
  startedAt: null,
  endedAt: null,
  ticker: [],
  lastLayers: {},
  traffic: {},
  events: 0,
})

const str = (v: unknown) => (typeof v === "string" ? v : "")
const KINDS: TaskKind[] = ["value", "interpretation", "records"]
const STATUSES: Status[] = ["supported", "not_found", "uncertain", "failed"]
/** An outcome the graph can place; anything unexpected is shown as failed rather than breaking the picture. */
const status = (v: unknown): Status => STATUSES.find((s) => s === v) ?? "failed"

function move(f: FieldState, node: NodeId, at: number, text: string, tone: Visit["tone"] = "info"): FieldState {
  if (f.node === node && f.visits.length) {
    return { ...f, visits: [...f.visits, { node, at, text, tone }] }
  }
  return { ...f, from: f.node, node, moves: f.moves + 1, visits: [...f.visits, { node, at, text, tone }] }
}

/** Pure reducer. `now` is the event time in seconds (the engine's clock, or the player's for a replay). */
export function reduce(state: PipelineState, e: EngineEvent & { data?: Record<string, number | boolean | string> }): PipelineState {
  const next: PipelineState = {
    ...state,
    events: state.events + 1,
    stageAt: { ...state.stageAt, [e.stage]: e.time },
    ticker: [...state.ticker.slice(-3), { seq: e.seq, level: e.level, stage: e.stage, message: e.message }],
  }
  if (e.kind === "run_start") return { ...initialState(), running: true, startedAt: e.time, stageAt: { run: e.time }, events: 1 }
  if (e.kind === "run_end") return { ...next, running: false, endedAt: e.time }
  if (e.kind !== "trace" || !e.data) return next

  const d = e.data
  const task = str(d.task)
  const phase = str(d.phase)
  const st = str(d.state)
  if (!task) return next

  let f = next.fields[task]
  if (!f) {
    const kind = KINDS.find((k) => k === str(d.kind)) ?? "value"
    f = { id: task, kind, after: str(d.after).split(",").filter(Boolean), node: "scope", from: null, moves: 0, layers: {}, loops: 0, action: null, outcome: null, decided: null, visits: [] }
    next.order = [...next.order, task]
    if (phase !== "declared") f = move(f, "scope", e.time, "declared")
  }
  const kind = KINDS.find((k) => k === str(d.kind))
  if (kind && f.kind !== kind) f = { ...f, kind }
  const before = f.node
  const tone = (e.level === "warn" ? "warn" : e.level === "error" ? "error" : e.level === "ok" ? "ok" : "info") as Visit["tone"]

  switch (phase) {
    case "declared":
      f = { ...f, visits: [{ node: "scope", at: e.time, text: "Declared in the spec", tone: "info" }] }
      break
    case "retrieve":
      f = move(f, "retrieve", e.time, st === "start" ? "Gathering evidence" : `Evidence ready${typeof d.passages === "number" ? ` (${d.passages} passages)` : ""}`, st === "done" ? "ok" : "info")
      break
    case "read": {
      const label = f.kind === "interpretation" ? "Interpreting" : f.kind === "records" ? "Reading every record" : "Extracting"
      f = move(f, "read", e.time, st === "start" ? label : `${label.replace(/ing\b/, "ed").replace("Reading every record", "Records read")} (${str(d.found) || "done"})`, st === "done" ? "ok" : "info")
      break
    }
    case "validate":
      if (st === "start") {
        f = move({ ...f, layers: {} }, "validate", e.time, f.loops ? "Validating again" : "Validating")
        if (before === "recover") f = { ...f, loops: f.loops }
      } else {
        const layer = str(d.layer)
        const found = typeof d.findings === "number" ? d.findings : 0
        f = { ...move(f, "validate", e.time, `${layer}: ${found ? `${found} finding(s)` : "ok"}`, found ? "warn" : "ok"), layers: { ...f.layers, [layer]: found } }
        next.lastLayers = f.layers
      }
      break
    case "decide": {
      const out = status(d.outcome)
      f = { ...move(f, "decide", e.time, `Decision: ${out.replace("_", " ")}`, out === "supported" || out === "not_found" ? "ok" : "warn"), decided: out }
      break
    }
    case "recover":
      if (st === "start") {
        f = { ...move(f, "recover", e.time, `Recovery: ${str(d.action).replace(/_/g, " ")}`, "warn"), action: str(d.action), loops: f.loops + 1 }
      } else {
        f = { ...f, visits: [...f.visits, { node: "recover", at: e.time, text: `${str(d.action).replace(/_/g, " ")} → ${str(d.outcome).replace("_", " ")}, ${d.kept ? "kept" : "not kept"}`, tone: d.kept ? "ok" : "warn" }] }
      }
      break
    case "result": {
      const out = status(d.outcome)
      f = { ...move(f, "result", e.time, `Result: ${out.replace("_", " ")}`, tone), outcome: out }
      break
    }
  }
  next.fields = { ...next.fields, [task]: f }
  if (f.node !== before) next.traffic = { ...next.traffic, [`${before}>${f.node}`]: e.time }
  return next
}
