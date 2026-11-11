// A scripted, SIMULATED engine run for previewing the graph without spending a model call.
// It uses the same event shapes as the engine's trace events, so it drives the exact same code path.
// The UI always labels it "Simulated".
import type { EngineEvent } from "@/lib/types"

type Ev = Omit<EngineEvent, "seq" | "time" | "run"> & { data?: Record<string, number | boolean | string> }
export interface Beat {
  /** seconds after the start of the run */
  at: number
  ev: Ev
}

const tr = (task: string, phase: string, state: string, extra: Record<string, number | boolean | string> = {}, level: EngineEvent["level"] = "info", message?: string): Ev => ({
  level,
  stage: { declared: "S02", retrieve: "S06", read: "S07", validate: "S10", decide: "S11", recover: "S12", result: "S13" }[phase] ?? "run",
  message: message ?? `${task}: ${phase} ${state}`,
  kind: "trace",
  data: { task, phase, state, ...extra },
})

const LAYERS = ["structure", "source grounding", "normalization", "consistency", "coverage"]
const layers = (task: string, t0: number, bad: Record<string, number> = {}): Beat[] =>
  LAYERS.map((l, i) => ({ at: t0 + i * 0.28, ev: tr(task, "validate", "note", { layer: l, findings: bad[l] ?? 0 }, bad[l] ? "warn" : "info", `${task}: ${l}: ${bad[l] ? "finding" : "ok"}`) }))

/** Five fields: a normalized date, a conditional approval, a list of obligations, a field rescued by recovery, one with too little evidence. */
export function demoScript(): Beat[] {
  const b: Beat[] = [{ at: 0, ev: { level: "info", stage: "run", message: "Simulated run started", kind: "run_start" } }]
  const fields: [string, string, string][] = [
    ["effective_date", "value", ""],
    ["approval", "interpretation", ""],
    ["obligations", "records", ""],
    ["notice_period", "value", ""],
    ["governing_law", "value", ""],
  ]
  fields.forEach(([id, kind, after], i) => b.push({ at: 0.15 + i * 0.12, ev: tr(id, "declared", "done", { kind, after }, "info", `${id}: ${kind} task declared`) }))
  // evidence
  fields.forEach(([id], i) => {
    b.push({ at: 1.1 + i * 0.35, ev: tr(id, "retrieve", "start") })
    b.push({ at: 1.9 + i * 0.35, ev: tr(id, "retrieve", "done", { passages: 6 + i }, "ok") })
  })
  // reading
  const read = (id: string, kind: string, s: number, e: number, found = "found"): Beat[] => [
    { at: s, ev: tr(id, "read", "start", { kind }) },
    { at: e, ev: tr(id, "read", "done", { kind, found }, "ok") },
  ]
  b.push(...read("effective_date", "value", 4.0, 5.3), ...read("notice_period", "value", 4.0, 5.3, "found"), ...read("governing_law", "value", 4.1, 5.4, "uncertain"))
  b.push(...read("approval", "interpretation", 4.3, 6.4), ...read("obligations", "records", 4.5, 7.4))

  // validation and decisions
  b.push({ at: 5.5, ev: tr("effective_date", "validate", "start") }, ...layers("effective_date", 5.7))
  b.push({ at: 7.2, ev: tr("effective_date", "decide", "done", { outcome: "supported", blocking: 0 }, "ok") })
  b.push({ at: 7.9, ev: tr("effective_date", "result", "done", { outcome: "supported", attempts: 0 }, "ok") })

  b.push({ at: 5.6, ev: tr("notice_period", "validate", "start") }, ...layers("notice_period", 5.8, { "source grounding": 1 }))
  b.push({ at: 7.4, ev: tr("notice_period", "decide", "done", { outcome: "uncertain", blocking: 1 }, "warn") })
  // recovery: bigger evidence budget, then validated again
  b.push({ at: 8.2, ev: tr("notice_period", "recover", "start", { action: "widen_evidence" }) })
  b.push({ at: 9.6, ev: tr("notice_period", "validate", "start") }, ...layers("notice_period", 9.8))
  b.push({ at: 11.3, ev: tr("notice_period", "decide", "done", { outcome: "supported", blocking: 0 }, "ok") })
  b.push({ at: 11.6, ev: tr("notice_period", "recover", "done", { action: "widen_evidence", outcome: "supported", kept: true }, "ok") })
  b.push({ at: 12.3, ev: tr("notice_period", "result", "done", { outcome: "supported", attempts: 1 }, "ok") })

  b.push({ at: 5.8, ev: tr("governing_law", "validate", "start") }, ...layers("governing_law", 6.0, { coverage: 2 }))
  b.push({ at: 7.6, ev: tr("governing_law", "decide", "done", { outcome: "uncertain", blocking: 1 }, "warn") })
  b.push({ at: 8.6, ev: tr("governing_law", "recover", "start", { action: "wider_retrieval" }) })
  b.push({ at: 10.4, ev: tr("governing_law", "validate", "start") }, ...layers("governing_law", 10.6, { coverage: 2 }))
  b.push({ at: 12.0, ev: tr("governing_law", "decide", "done", { outcome: "uncertain", blocking: 1 }, "warn") })
  b.push({ at: 12.3, ev: tr("governing_law", "recover", "done", { action: "wider_retrieval", outcome: "uncertain", kept: false }, "warn") })
  b.push({ at: 13.0, ev: tr("governing_law", "result", "done", { outcome: "uncertain", attempts: 1 }, "warn") })

  b.push({ at: 6.6, ev: tr("approval", "validate", "start") }, ...layers("approval", 6.8))
  b.push({ at: 8.4, ev: tr("approval", "decide", "done", { outcome: "supported", blocking: 0 }, "ok") })
  b.push({ at: 9.0, ev: tr("approval", "result", "done", { outcome: "supported", attempts: 0 }, "ok") })

  b.push({ at: 7.5, ev: tr("obligations", "validate", "start", { kind: "records" }) })
  b.push({ at: 8.9, ev: tr("obligations", "decide", "done", { outcome: "supported", kind: "records" }, "ok") })
  b.push({ at: 9.6, ev: tr("obligations", "result", "done", { outcome: "supported", attempts: 0 }, "ok") })

  b.push({ at: 13.8, ev: { level: "ok", stage: "run", message: "Simulated run finished", kind: "run_end" } })
  return b.sort((a, c) => a.at - c.at)
}
