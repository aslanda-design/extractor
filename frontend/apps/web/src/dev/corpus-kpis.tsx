import type { ReactNode } from "react"

import { cn } from "@workspace/ui/lib/utils"

// The headline numbers of a corpus measure, as tiles and small tables. Shapes follow ui/service.py (corpus_acceptance,
// corpus_records, corpus_recovery); anything missing is simply not drawn, so an older engine still shows the full result below.

type Tone = "ok" | "warn" | "bad" | "plain"

interface AcceptanceRow {
  profile: string
  tasks: number
  automation_rate: number | null
  accepted_precision: number | null
  counts: Record<string, number>
  mean_seconds: number
}
interface AcceptanceSplit {
  split: string
  tasks: number
  automation_rate: number | null
  accepted_precision: number | null
  false_accepts: number
  false_absences: number
  over_abstentions: number
}
interface Miss {
  case_id: string
  task_id: string
  outcome: string
  split?: string
  status: string
  expected: unknown
  value: unknown
  proposal_value: unknown
  reasons?: string[]
}
interface AcceptanceResult {
  rows?: AcceptanceRow[]
  by_split?: AcceptanceSplit[]
  misses?: Miss[]
}

interface RecordTotals {
  tasks: number
  gold: number
  found: number
  matched: number
  recall: number | null
  precision: number | null
  field_exact: number | null
  field_near: number | null
  supported: number
  complete_checked: number
  seconds: number
}
interface RecordScore {
  case_id: string
  task_id: string
  split?: string
  gold: number
  found: number
  recall: number
  precision: number
  fields_total: number
  fields_exact: number
  status: string
  completeness: string
  seconds: number
}
interface RecordsResult {
  totals?: RecordTotals
  by_split?: Record<string, RecordTotals>
  scores?: RecordScore[]
  causes?: { cause: string }[]
}

type Counts = Record<string, number>
interface ActionRow {
  action: string
  attempts: number
  kept: number
  rescues: number
  harm: number
  calls: number
  seconds: number
}
interface Changed {
  case_id: string
  task_id: string
  split: string
  before: string
  after: string
  effect: string
  actions: string[]
  kept: string[]
  calls: number
  note: string
}
interface RecoveryResult {
  tasks?: number
  before?: Counts
  after?: Counts
  actions?: ActionRow[]
  changed?: Changed[]
  calls_total?: number
  calls_recovery?: number
  enabled?: boolean
  allowed?: string[]
}

const pct = (v: number | null | undefined) => (v === null || v === undefined ? "n/a" : `${Math.round(v * 100)}%`)
const words = (s: string) => s.replace(/_/g, " ")
const show = (v: unknown) => (v === null || v === undefined ? "" : typeof v === "string" ? v : JSON.stringify(v))

const TONE: Record<Tone, string> = { ok: "text-ok", warn: "text-warn", bad: "text-bad", plain: "" }

function Tile({ label, value, hint, tone = "plain" }: { label: string; value: ReactNode; hint: string; tone?: Tone }) {
  return (
    <div className="bg-card rounded-lg border p-3">
      <p className="text-muted-foreground text-xs">{label}</p>
      <p className={cn("tabular mt-1 text-2xl font-semibold", TONE[tone])}>{value}</p>
      <p className="text-muted-foreground mt-1 text-xs">{hint}</p>
    </div>
  )
}

function Table({ head, rows }: { head: string[]; rows: ReactNode[][] }) {
  if (!rows.length) return null
  return (
    <div className="bg-card overflow-auto rounded-md border">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b text-left">
            {head.map((h) => (
              <th key={h} scope="col" className="text-muted-foreground px-2 py-1.5 font-medium">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((cells, i) => (
            <tr key={i} className="border-b last:border-0">
              {cells.map((c, j) => (
                <td key={j} className={cn("px-2 py-1.5 align-top", j > 0 && "tabular")}>
                  {c}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Mark({ tone, children }: { tone: Tone; children: ReactNode }) {
  const soft = { ok: "bg-ok-soft text-ok", warn: "bg-warn-soft text-warn", bad: "bg-bad-soft text-bad", plain: "bg-secondary" }[tone]
  return <span className={cn("rounded px-1.5 py-0.5 text-xs font-medium whitespace-nowrap", soft)}>{children}</span>
}

const errorTone = (n: number): Tone => (n > 0 ? "bad" : "ok")

function AcceptanceKpis({ r }: { r: AcceptanceResult }) {
  const row = r.rows?.[0]
  if (!row) return null
  const falseAccepts = row.counts.false_accept ?? 0
  const falseAbsences = row.counts.false_absence ?? 0
  const serious = (m: Miss) => m.outcome === "false_accept" || m.outcome === "false_absence"
  const misses = [...(r.misses ?? [])].sort((a, b) => Number(serious(b)) - Number(serious(a)))
  return (
    <div className="space-y-3">
      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-5">
        <Tile label="Automation" value={pct(row.automation_rate)} hint={`of ${row.tasks} tasks the engine accepted without review`} />
        <Tile
          label="Accepted precision"
          value={pct(row.accepted_precision)}
          hint="of the accepted answers match the label"
          tone={row.accepted_precision !== null && row.accepted_precision < 1 ? "warn" : "ok"}
        />
        <Tile label="False accepts" value={falseAccepts} hint="accepted answers that are wrong" tone={errorTone(falseAccepts)} />
        <Tile label="False absences" value={falseAbsences} hint={'said "not found" where the label says otherwise'} tone={errorTone(falseAbsences)} />
        <Tile label="Over-abstentions" value={row.counts.over_abstention ?? 0} hint="withheld although a right answer existed (costs a review)" />
      </div>
      <Table
        head={["Split", "Tasks", "Automation", "Accepted precision", "False accepts", "False absences", "Over-abstentions"]}
        rows={(r.by_split ?? []).map((s) => [
          <span className="font-medium">{s.split}</span>,
          s.tasks,
          pct(s.automation_rate),
          pct(s.accepted_precision),
          s.false_accepts,
          s.false_absences,
          s.over_abstentions,
        ])}
      />
      {(r.by_split?.length ?? 0) > 1 && (
        <p className="text-muted-foreground text-xs">Fixes are designed on train and validation cases. The test row is the number to report.</p>
      )}
      <Table
        head={["Case", "Task", "Outcome", "Expected", "Engine", "Why"]}
        rows={misses.map((m) => [
          m.case_id,
          <span className="font-mono">{m.task_id}</span>,
          <Mark tone={serious(m) ? "bad" : "warn"}>{words(m.outcome)}</Mark>,
          show(m.expected),
          `${m.status}${m.value !== null && m.value !== undefined ? ` ${show(m.value)}` : m.proposal_value !== null && m.proposal_value !== undefined ? ` (proposed ${show(m.proposal_value)})` : ""}`,
          <span className="text-muted-foreground">{(m.reasons ?? [])[0] ?? ""}</span>,
        ])}
      />
    </div>
  )
}

function RecordsKpis({ r }: { r: RecordsResult }) {
  const t = r.totals
  if (!t) return null
  const causes = new Map<string, number>()
  for (const c of r.causes ?? []) causes.set(c.cause, (causes.get(c.cause) ?? 0) + 1)
  return (
    <div className="space-y-3">
      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-5">
        <Tile label="Record recall" value={pct(t.recall)} hint={`${t.matched} of ${t.gold} labeled records found`} />
        <Tile label="Record precision" value={pct(t.precision)} hint={`${t.matched} of ${t.found} found records are labeled`} />
        <Tile label="Field exact" value={pct(t.field_exact)} hint={`fields of matched records equal to the label (near: ${pct(t.field_near)})`} />
        <Tile label="Collections accepted" value={`${t.supported} / ${t.tasks}`} hint="complete and every required field accepted" />
        <Tile label="Completeness checked" value={`${t.complete_checked} / ${t.tasks}`} hint="the audit found no gap in the scope" />
      </div>
      <Table
        head={["Split", "Tasks", "Recall", "Precision", "Field exact", "Accepted"]}
        rows={Object.entries(r.by_split ?? {}).map(([split, s]) => [
          <span className="font-medium">{split}</span>,
          s.tasks,
          `${pct(s.recall)} (${s.matched}/${s.gold})`,
          pct(s.precision),
          pct(s.field_exact),
          s.supported,
        ])}
      />
      <Table
        head={["Case", "Task", "Labeled", "Found", "Recall", "Precision", "Field exact", "Status", "Completeness", "Seconds"]}
        rows={(r.scores ?? []).map((s) => [
          s.case_id,
          <span className="font-mono">{s.task_id}</span>,
          s.gold,
          s.found,
          pct(s.recall),
          pct(s.precision),
          s.fields_total ? pct(s.fields_exact / s.fields_total) : "n/a",
          <Mark tone={s.status === "supported" ? "ok" : s.status === "failed" ? "bad" : "warn"}>{words(s.status)}</Mark>,
          s.completeness,
          Math.round(s.seconds),
        ])}
      />
      {causes.size > 0 && (
        <p className="text-xs">
          <span className="text-muted-foreground">Why the problems happen: </span>
          {[...causes.entries()]
            .sort((a, b) => b[1] - a[1])
            .map(([cause, n]) => `${words(cause)} ${n}`)
            .join(" · ")}
        </p>
      )}
    </div>
  )
}

function RecoveryKpis({ r }: { r: RecoveryResult }) {
  if (r.tasks === undefined || !r.before || !r.after) return null
  const tasks = r.tasks
  const accepted = (c: Counts) => (c.accepted_correct ?? 0) + (c.false_accept ?? 0)
  const line = (name: string, c: Counts) => [
    <span className="font-medium">{name}</span>,
    pct(tasks ? accepted(c) / tasks : null),
    pct(accepted(c) ? (c.accepted_correct ?? 0) / accepted(c) : null),
    c.false_accept ?? 0,
    c.false_absence ?? 0,
    c.failed ?? 0,
  ]
  const rescues = (r.actions ?? []).reduce((n, a) => n + a.rescues, 0)
  const harm = (r.actions ?? []).reduce((n, a) => n + a.harm, 0)
  const tone = (effect: string): Tone => (effect === "rescue" ? "ok" : effect === "harm" ? "bad" : "plain")
  return (
    <div className="space-y-3">
      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
        <Tile label="Rescues" value={rescues} hint="not accepted before, right after recovery" tone={rescues > 0 ? "ok" : "plain"} />
        <Tile label="Harm" value={harm} hint="a new false accept or false absence" tone={errorTone(harm)} />
        <Tile label="Tasks recovery touched" value={r.changed?.length ?? 0} hint={`of ${tasks} labeled tasks`} />
        <Tile label="Model calls spent" value={r.calls_recovery ?? 0} hint={`on recovery, of ${r.calls_total ?? 0} new calls in the run`} />
      </div>
      <p className="text-muted-foreground text-xs">
        Recovery is {r.enabled === false ? "off" : "on"}. Allowed actions: {(r.allowed ?? []).join(", ") || "none"}.
      </p>
      <Table
        head={["", "Automation", "Accepted precision", "False accepts", "False absences", "Failed"]}
        rows={[line("Before recovery", r.before), line("After recovery", r.after)]}
      />
      <Table
        head={["Action", "Attempts", "Kept", "Rescues", "Harm", "Calls", "Seconds", "Calls per rescue"]}
        rows={(r.actions ?? []).map((a) => [
          <span className="font-mono">{a.action}</span>,
          a.attempts,
          a.kept,
          a.rescues,
          a.harm ? <Mark tone="bad">{a.harm}</Mark> : 0,
          a.calls,
          Math.round(a.seconds),
          a.rescues ? (a.calls / a.rescues).toFixed(1) : "n/a",
        ])}
      />
      {(r.actions?.length ?? 0) === 0 && <p className="text-sm">No task needed recovery, or no allowed action applied to the tasks that were not accepted.</p>}
      <Table
        head={["Case", "Task", "Split", "Before", "After", "Actions", "Calls", "Effect"]}
        rows={(r.changed ?? []).map((c) => [
          c.case_id,
          <span className="font-mono">{c.task_id}</span>,
          c.split,
          words(c.before),
          words(c.after),
          c.actions.join(", "),
          c.calls,
          <span>
            <Mark tone={tone(c.effect)}>{c.effect}</Mark> <span className="text-muted-foreground">{c.note}</span>
          </span>,
        ])}
      />
      <p className="text-muted-foreground text-xs">Keep an action on only if it rescues with no harm. Judge the test split separately.</p>
    </div>
  )
}

/** The KPIs of a finished corpus measure. Returns null for measures without a dedicated view (the generic table covers them). */
export function CorpusKpis({ measure, value }: { measure: string; value: unknown }) {
  if (value === null || typeof value !== "object") return null
  if (measure === "acceptance") return <AcceptanceKpis r={value as AcceptanceResult} />
  if (measure === "records") return <RecordsKpis r={value as RecordsResult} />
  if (measure === "recovery") return <RecoveryKpis r={value as RecoveryResult} />
  return null
}

export const hasKpis = (measure: string) => ["acceptance", "records", "recovery"].includes(measure)
