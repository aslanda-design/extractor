import { useCallback, useEffect, useState } from "react"
import { Loader2, Play } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { cn } from "@workspace/ui/lib/utils"
import { CorpusKpis, hasKpis } from "./corpus-kpis"
import { get, post, type CorpusStatus } from "./dev-api"
import { JsonView, MetricsTable } from "./json-view"

const MEASURES = [
  { id: "parsing", label: "Parsing quality", hint: "Quote recall, page accuracy, unresolved issues. No model calls.", filters: false },
  { id: "indexing", label: "Chunking quality", hint: "Share of labeled quotes that sit inside one passage.", filters: false },
  { id: "overlap", label: "Evidence overlap", hint: "How much evidence value tasks share. Retrieval only.", filters: true },
  { id: "extraction", label: "Value extraction", hint: "Correct, wrong, false absence, false value. Calls the model.", filters: true },
  { id: "acceptance", label: "Acceptance", hint: "Automation rate, accepted-answer precision, false accepts.", filters: true },
  { id: "records", label: "Records", hint: "Recall, precision, field accuracy, completeness.", filters: true },
  { id: "recovery", label: "Recovery", hint: "Rescues, harm and calls per action.", filters: true },
] as const

const now = () => performance.now()

interface Outcome {
  measure: string
  seconds: number
  value?: unknown
  error?: string
}

/** Runs the corpus measures of the engine (labeled evaluation data) and shows their results. */
export function CorpusView() {
  const [status, setStatus] = useState<CorpusStatus | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [tags, setTags] = useState<string[]>([])
  const [kinds, setKinds] = useState<string[]>([])
  const [cases, setCases] = useState<string[]>([])
  const [busy, setBusy] = useState<string | null>(null)
  const [outcome, setOutcome] = useState<Outcome | null>(null)

  const refresh = useCallback(() => {
    get<CorpusStatus>("/api/corpus").then(setStatus, (e: Error) => setLoadError(e.message))
  }, [])
  useEffect(() => {
    // initial load of the corpus status; state is set after the fetch resolves
    refresh()
  }, [refresh])

  const toggle = (list: string[], set: (v: string[]) => void, v: string) => set(list.includes(v) ? list.filter((x) => x !== v) : [...list, v])

  const measure = async (id: string) => {
    setBusy(id)
    setOutcome(null)
    const t0 = now()
    try {
      const body = id === "parsing" || id === "indexing" ? {} : id === "records" ? { tags, cases } : { tags, cases, kinds }
      const value = await post(`/api/corpus/${id}`, body)
      setOutcome({ measure: id, seconds: (now() - t0) / 1000, value })
    } catch (e) {
      setOutcome({ measure: id, seconds: (now() - t0) / 1000, error: (e as Error).message })
    } finally {
      setBusy(null)
    }
  }

  const chip = (on: boolean) =>
    cn("rounded-full border px-2.5 py-1 text-xs transition-colors", on ? "border-primary bg-primary text-primary-foreground" : "bg-card hover:bg-secondary")

  if (loadError) return <p className="text-bad p-4 text-sm">Could not reach the engine: {loadError}</p>
  if (!status) return <p className="text-muted-foreground p-4 text-sm">Loading corpus...</p>
  if (!status.ok)
    return (
      <div className="p-4 text-sm">
        <p className="font-medium">The corpus has problems</p>
        <ul className="text-bad mt-2 list-disc pl-5">{status.problems?.map((p) => <li key={p}>{p}</li>)}</ul>
        <p className="text-muted-foreground mt-3">Human-labeled data goes in evaluation/corpus/ (see its README).</p>
      </div>
    )

  return (
    <div className="grid h-full min-h-0 gap-0 lg:grid-cols-[22rem_1fr]">
      <aside className="bg-card min-h-0 space-y-4 overflow-auto border-b p-4 lg:border-r lg:border-b-0">
        <section>
          <h2 className="text-sm font-semibold">Corpus</h2>
          <p className="text-muted-foreground mt-1 text-xs break-all">
            {status.cases?.length ?? 0} cases in {status.path}
          </p>
        </section>

        <section>
          <h3 className="text-xs font-semibold uppercase tracking-wide">Splits and tags</h3>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {status.tags?.map((t) => (
              <button key={t} type="button" className={chip(tags.includes(t))} onClick={() => toggle(tags, setTags, t)} aria-pressed={tags.includes(t)}>
                {t}
              </button>
            ))}
          </div>
          <p className="text-muted-foreground mt-1.5 text-xs">Design on train and validation. Report split:test separately.</p>
        </section>

        <section>
          <h3 className="text-xs font-semibold uppercase tracking-wide">Task kinds</h3>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {status.kinds?.map((k) => (
              <button
                key={k.kind}
                type="button"
                disabled={!k.implemented}
                className={cn(chip(kinds.includes(k.kind)), "disabled:opacity-40")}
                onClick={() => toggle(kinds, setKinds, k.kind)}
                aria-pressed={kinds.includes(k.kind)}
              >
                {k.kind} <span className="tabular opacity-70">{k.labeled}</span>
              </button>
            ))}
          </div>
          <p className="text-muted-foreground mt-1.5 text-xs">None selected means every implemented kind.</p>
        </section>

        <section>
          <h3 className="text-xs font-semibold uppercase tracking-wide">Cases</h3>
          <ul className="mt-2 space-y-1">
            {status.case_info?.map((c) => (
              <li key={c.id}>
                <label className="hover:bg-secondary flex cursor-pointer items-start gap-2 rounded px-1.5 py-1 text-xs">
                  <input type="checkbox" className="mt-0.5" checked={cases.includes(c.id)} onChange={() => toggle(cases, setCases, c.id)} />
                  <span className="min-w-0">
                    <span className="block truncate font-medium">{c.id}</span>
                    <span className="text-muted-foreground block truncate">
                      {c.documents.join(", ")} · {c.tags.join(" ")}
                    </span>
                  </span>
                </label>
              </li>
            ))}
          </ul>
          <p className="text-muted-foreground mt-1.5 text-xs">None ticked means all cases.</p>
        </section>
      </aside>

      <section className="min-h-0 space-y-4 overflow-auto p-4">
        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
          {MEASURES.map((m) => (
            <div key={m.id} className="bg-card flex flex-col gap-2 rounded-lg border p-3">
              <div>
                <h3 className="text-sm font-semibold">{m.label}</h3>
                <p className="text-muted-foreground mt-0.5 text-xs">{m.hint}</p>
              </div>
              <Button size="sm" variant="outline" className="mt-auto self-start" disabled={busy !== null} onClick={() => void measure(m.id)}>
                {busy === m.id ? <Loader2 className="animate-spin" aria-hidden /> : <Play aria-hidden />}
                {busy === m.id ? "Running" : "Measure"}
              </Button>
            </div>
          ))}
        </div>

        {busy && <p className="text-muted-foreground text-sm">Running {busy}. Watch the Logs tab for the engine steps as they happen.</p>}
        {outcome?.error && (
          <p role="alert" className="bg-bad-soft text-bad rounded-md px-3 py-2 text-sm">
            {outcome.measure} failed: {outcome.error}
          </p>
        )}
        {outcome && !outcome.error && (
          <div className="space-y-3">
            <h3 className="text-sm font-semibold">
              {outcome.measure} <span className="tabular text-muted-foreground font-normal">· {outcome.seconds.toFixed(1)}s</span>
            </h3>
            <CorpusKpis measure={outcome.measure} value={outcome.value} />
            {!hasKpis(outcome.measure) && <MetricsTable value={outcome.value} />}
            <details>
              <summary className="text-muted-foreground cursor-pointer text-xs">Full result</summary>
              <JsonView value={outcome.value} className="mt-2" />
            </details>
          </div>
        )}
        <p className="text-muted-foreground text-xs">Every finished measure is also saved in History, so it survives a reload.</p>
      </section>
    </div>
  )
}
