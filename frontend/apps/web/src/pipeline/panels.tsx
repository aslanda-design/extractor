import { useEffect, useMemo, useState } from "react"

import { cn } from "@workspace/ui/lib/utils"
import type { Status } from "@/lib/types"
import type { FieldState, NodeId, PipelineState } from "./model"
import { ErrorBoundary } from "@/components/error-boundary"
import { KIND_COLOR, PipelineGraph, STATUS_COLOR } from "./pipeline-graph"

const STAGES: [string, string][] = [
  ["S01", "Scope"],
  ["S02", "Spec"],
  ["S03", "Parse"],
  ["S04", "Index"],
  ["S05", "Plan"],
  ["S06", "Retrieve"],
  ["S07", "Extract"],
  ["S08", "Interpret"],
  ["S09", "Records"],
  ["S10", "Validate"],
  ["S11", "Decide"],
  ["S12", "Recover"],
  ["S13", "Result"],
]

export const NODE_LABEL: Record<NodeId, string> = {
  scope: "Scope",
  retrieve: "Retrieving evidence",
  read: "Reading",
  validate: "Validating",
  decide: "Deciding",
  recover: "Recovering",
  result: "Done",
}

const wall = () => Date.now() / 1000

/** The stage rail, the graph and the legend. `dark` makes it an always-dark instrument; otherwise it follows the app theme. */
export function PipelineStage({
  state,
  selectedId,
  hoverId,
  onSelect,
  dark,
  className,
}: {
  state: PipelineState
  selectedId: string | null
  hoverId: string | null
  onSelect: (id: string | null) => void
  dark?: boolean
  className?: string
}) {
  const [now, setNow] = useState(wall)
  useEffect(() => {
    const t = window.setInterval(() => setNow(wall()), 400)
    return () => clearInterval(t)
  }, [])
  const elapsed = state.startedAt ? Math.max(0, (state.endedAt ?? now) - state.startedAt) : 0
  return (
    <section
      aria-label="Pipeline graph"
      className={cn("relative flex min-h-0 min-w-0 flex-col overflow-hidden", dark && "dark rounded-xl border", className)}
      style={{
        background: dark
          ? "radial-gradient(900px 420px at 28% -8%, color-mix(in oklch, var(--primary) 16%, var(--background)), var(--background) 70%)"
          : "radial-gradient(900px 360px at 28% -10%, color-mix(in oklch, var(--primary) 9%, var(--card)), var(--card) 70%)",
        color: "var(--foreground)",
      }}
    >
      <div className="flex items-center gap-3 px-4 pt-3">
        <nav className="flex min-w-0 flex-1 flex-wrap gap-x-3 gap-y-1" aria-label="Engine stages">
          {STAGES.map(([code, label]) => {
            const at = state.stageAt[code]
            const lit = at !== undefined && now - at < 3
            return (
              <span key={code} className={cn("flex items-center gap-1.5 text-[11px] transition-colors", lit ? "text-foreground" : "text-muted-foreground/70")}>
                <span
                  className="size-1.5 rounded-full transition-all duration-300"
                  style={{ background: lit ? "var(--primary)" : "var(--border)", boxShadow: lit ? "0 1px 6px color-mix(in oklch, var(--primary) 80%, transparent)" : "none" }}
                />
                {label}
              </span>
            )
          })}
        </nav>
        <span className="tabular text-muted-foreground shrink-0 text-xs">{elapsed.toFixed(1)}s</span>
      </div>
      <div className="flex min-h-0 flex-1 items-center px-2">
        <ErrorBoundary label="The pipeline graph hit an error. The extraction itself is not affected.">
          <PipelineGraph state={state} selectedId={selectedId} hoverId={hoverId} onSelect={onSelect} />
        </ErrorBoundary>
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t px-4 py-2 text-xs">
        {(["value", "interpretation", "records"] as const).map((k) => (
          <span key={k} className="text-muted-foreground flex items-center gap-1.5">
            <span className="size-2 rounded-full" style={{ background: KIND_COLOR[k] }} /> {k}
          </span>
        ))}
        <span className="text-muted-foreground ml-auto min-w-0 truncate font-mono text-[11px]">{state.ticker.at(-1)?.message ?? ""}</span>
      </div>
    </section>
  )
}

/** The fields of a run with where each one is now; selecting one shows its path through the pipeline. */
export function FieldList({
  state,
  selectedId,
  onSelect,
  onHover,
  className,
}: {
  state: PipelineState
  selectedId: string | null
  onSelect: (id: string | null) => void
  onHover: (id: string | null) => void
  className?: string
}) {
  const fields = useMemo(() => state.order.map((id) => state.fields[id]), [state.order, state.fields])
  const selected = selectedId ? state.fields[selectedId] : null
  const counts = useMemo(() => {
    const c: Record<Status, number> = { supported: 0, not_found: 0, uncertain: 0, failed: 0 }
    for (const f of fields) if (f.outcome) c[f.outcome]++
    return c
  }, [fields])
  return (
    <div className={cn("flex min-h-0 flex-col", className)} aria-label="Fields">
      <div className="flex items-center gap-2 border-b px-3 py-2">
        <h2 className="text-sm font-semibold">Fields</h2>
        <span className="tabular text-muted-foreground text-xs">{fields.length}</span>
        <span className="ml-auto flex gap-2 text-xs">
          {(Object.keys(counts) as Status[]).map((s) => (
            <span key={s} className="tabular flex items-center gap-1" title={s.replace("_", " ")}>
              <span className="size-2 rounded-full" style={{ background: STATUS_COLOR[s] }} />
              {counts[s]}
            </span>
          ))}
        </span>
      </div>
      <ul className="min-h-0 flex-1 overflow-auto">
        {fields.length === 0 && <li className="text-muted-foreground p-3 text-sm">Fields appear here as the engine declares them.</li>}
        {fields.map((f) => (
          <FieldRow key={f.id} f={f} selected={f.id === selectedId} onSelect={() => onSelect(f.id === selectedId ? null : f.id)} onHover={onHover} />
        ))}
      </ul>
      {selected && <FieldDetail f={selected} />}
    </div>
  )
}

function FieldRow({ f, selected, onSelect, onHover }: { f: FieldState; selected: boolean; onSelect: () => void; onHover: (id: string | null) => void }) {
  const color = f.outcome ? STATUS_COLOR[f.outcome] : KIND_COLOR[f.kind]
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        onMouseEnter={() => onHover(f.id)}
        onMouseLeave={() => onHover(null)}
        onFocus={() => onHover(f.id)}
        onBlur={() => onHover(null)}
        aria-pressed={selected}
        className={cn("hover:bg-secondary flex w-full items-center gap-2.5 border-b px-3 py-2 text-left transition-colors", selected && "bg-evidence-soft")}
      >
        <span className="size-2.5 shrink-0 rounded-full" style={{ background: color }} />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium">{f.id}</span>
          <span className="text-muted-foreground block truncate text-xs">
            {f.outcome ? f.outcome.replace("_", " ") : NODE_LABEL[f.node]}
            {f.loops > 0 && ` · ${f.loops} recovery ${f.loops === 1 ? "pass" : "passes"}`}
          </span>
        </span>
        <span className="text-muted-foreground shrink-0 text-[11px]">{f.kind}</span>
      </button>
    </li>
  )
}

const TONE = { info: "var(--muted-foreground)", ok: "var(--ok)", warn: "var(--warn)", error: "var(--bad)" }

function FieldDetail({ f }: { f: FieldState }) {
  const t0 = f.visits[0]?.at ?? 0
  return (
    <div className="max-h-[45%] shrink-0 overflow-auto border-t p-3">
      <h3 className="text-sm font-semibold">{f.id}</h3>
      <ol className="mt-2 space-y-1.5">
        {f.visits.map((v, i) => (
          <li key={i} className="pipe-rise flex gap-2 text-xs">
            <span className="mt-1.5 size-1.5 shrink-0 rounded-full" style={{ background: TONE[v.tone] }} />
            <span className="min-w-0 flex-1">{v.text}</span>
            <span className="tabular text-muted-foreground shrink-0">+{Math.max(0, v.at - t0).toFixed(1)}s</span>
          </li>
        ))}
      </ol>
    </div>
  )
}
