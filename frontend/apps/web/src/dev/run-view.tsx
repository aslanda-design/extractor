import { useEffect, useRef, useState } from "react"
import { CheckCircle2, Loader2, Play } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { Textarea } from "@workspace/ui/components/textarea"
import { cn } from "@workspace/ui/lib/utils"
import type { RunOutput } from "@/lib/types"
import { get, openEventStream, post, type DevEvent } from "./dev-api"
import { JsonView, MetricsTable } from "./json-view"
import { clock } from "./logs-view"

const now = () => performance.now()

interface Doc {
  name: string
  size: number
  parseable: boolean
}

const LEVEL: Record<string, string> = { debug: "text-muted-foreground", info: "text-foreground", ok: "text-ok", warn: "text-warn", error: "text-bad" }

/** Run one spec on chosen documents and watch only that run's engine events. */
export function RunView() {
  const [specs, setSpecs] = useState<string[]>([])
  const [docs, setDocs] = useState<Doc[]>([])
  const [spec, setSpec] = useState("")
  const [specText, setSpecText] = useState("")
  const [picked, setPicked] = useState<string[]>([])
  const [check, setCheck] = useState<{ ok: boolean; errors?: { message: string; path: string }[] } | null>(null)
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<RunOutput | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [events, setEvents] = useState<DevEvent[]>([])
  const [seconds, setSeconds] = useState<number | null>(null)
  const logBody = useRef<HTMLDivElement>(null)

  useEffect(() => {
    void Promise.all([get<string[]>("/api/specs"), get<Doc[]>("/api/documents")]).then(
      ([s, d]) => {
        setSpecs(s)
        setDocs(d)
      },
      (e: Error) => setError(e.message)
    )
  }, [])

  useEffect(() => {
    if (!spec) return
    void get<{ name: string; text: string }>(`/api/specs/${encodeURIComponent(spec)}`).then(
      (s) => {
        setSpecText(s.text)
        setCheck(null)
      },
      (e: Error) => setError(e.message)
    )
  }, [spec])

  useEffect(() => {
    const el = logBody.current
    if (el) el.scrollTop = el.scrollHeight
  }, [events.length])

  const validate = () => {
    post<typeof check>("/api/specs/validate", { text: specText }).then(setCheck, (e: Error) => setError(e.message))
  }

  const start = async () => {
    setError(null)
    setResult(null)
    setEvents([])
    setRunning(true)
    const t0 = now()
    const runs = new Set<string>()
    let close = () => {}
    try {
      const since = (await get<{ last: number }>("/api/logs?since=2147483647")).last
      close = openEventStream(
        since,
        (e) => {
          if (e.kind === "run_start") runs.add(e.run)
          if (runs.has(e.run)) setEvents((prev) => [...prev, e])
        },
        () => {}
      )
      setResult(await post<RunOutput>("/api/run", { spec, documents: picked }))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSeconds((now() - t0) / 1000)
      setRunning(false)
      setTimeout(close, 1200)
    }
  }

  const statuses = result?.summary?.statuses ?? {}
  return (
    <div className="grid h-full min-h-0 lg:grid-cols-[24rem_1fr]">
      <aside className="bg-card min-h-0 space-y-4 overflow-auto border-b p-4 lg:border-r lg:border-b-0">
        <section>
          <label htmlFor="dev-spec" className="text-xs font-semibold uppercase tracking-wide">
            Specification
          </label>
          <select id="dev-spec" className="bg-background mt-1.5 h-8 w-full rounded-md border px-2 text-sm" value={spec} onChange={(e) => setSpec(e.target.value)}>
            <option value="">Choose a spec</option>
            {specs.map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
          {spec && (
            <>
              <Textarea className="mt-2 h-44 font-mono text-xs" value={specText} onChange={(e) => setSpecText(e.target.value)} spellCheck={false} aria-label="Spec YAML" />
              <div className="mt-2 flex items-center gap-2">
                <Button size="sm" variant="outline" onClick={validate}>
                  Validate
                </Button>
                {check?.ok && (
                  <span className="text-ok flex items-center gap-1 text-xs">
                    <CheckCircle2 className="size-3.5" aria-hidden /> Valid
                  </span>
                )}
              </div>
              {check && !check.ok && (
                <ul className="text-bad mt-2 list-disc pl-5 text-xs">
                  {check.errors?.map((e, i) => (
                    <li key={i}>
                      <span className="font-mono">{e.path}</span> {e.message}
                    </li>
                  ))}
                </ul>
              )}
              <p className="text-muted-foreground mt-1.5 text-xs">Edits here are only validated. The run uses the saved spec.</p>
            </>
          )}
        </section>

        <section>
          <h3 className="text-xs font-semibold uppercase tracking-wide">Documents</h3>
          {docs.length === 0 && <p className="text-muted-foreground mt-1.5 text-xs">No uploaded documents. Upload one with New extraction in the review screen.</p>}
          <ul className="mt-1.5 space-y-0.5">
            {docs.map((d) => (
              <li key={d.name}>
                <label className="hover:bg-secondary flex cursor-pointer items-center gap-2 rounded px-1.5 py-1 text-xs">
                  <input
                    type="checkbox"
                    checked={picked.includes(d.name)}
                    onChange={() => setPicked(picked.includes(d.name) ? picked.filter((x) => x !== d.name) : [...picked, d.name])}
                  />
                  <span className="min-w-0 flex-1 truncate">{d.name}</span>
                  <span className="text-muted-foreground tabular">{Math.round(d.size / 1024)} KB</span>
                </label>
              </li>
            ))}
          </ul>
        </section>

        <Button disabled={running || !spec || picked.length === 0} onClick={() => void start()}>
          {running ? <Loader2 className="animate-spin" aria-hidden /> : <Play aria-hidden />}
          {running ? "Running" : "Run extraction"}
        </Button>
      </aside>

      <section className="grid min-h-0 grid-rows-[minmax(0,1fr)_minmax(0,1fr)]">
        <div className="min-h-0 space-y-3 overflow-auto p-4">
          {error && (
            <p role="alert" className="bg-bad-soft text-bad rounded-md px-3 py-2 text-sm">
              {error}
            </p>
          )}
          {!result && !error && <p className="text-muted-foreground text-sm">Pick a spec and documents, then run. The result and the cost of the run appear here.</p>}
          {result && (
            <>
              <div className="flex flex-wrap gap-2 text-xs">
                {Object.entries(statuses).map(([k, v]) => (
                  <span key={k} className="bg-secondary rounded-full px-2.5 py-1">
                    {k.replace("_", " ")} <span className="tabular font-semibold">{v}</span>
                  </span>
                ))}
                {seconds !== null && <span className="bg-secondary tabular rounded-full px-2.5 py-1">{seconds.toFixed(1)}s total</span>}
              </div>
              <MetricsTable value={result.summary} />
              <JsonView value={result} />
            </>
          )}
        </div>
        <div className="bg-card flex min-h-0 flex-col border-t">
          <div className="flex items-center gap-2 border-b px-3 py-1.5 text-xs font-medium">
            Run logs
            <span className="text-muted-foreground font-normal">{running ? "live" : events.length ? "last run" : ""}</span>
            <span className="tabular text-muted-foreground ml-auto font-normal">{events.length} lines</span>
          </div>
          <div ref={logBody} className="min-h-0 flex-1 overflow-auto px-3 py-2 font-mono text-xs leading-relaxed" role="log">
            {events.length === 0 && <p className="text-muted-foreground font-sans">Only this run's events show here.</p>}
            {events.map((e) => (
              <div key={e.seq} className={cn("flex gap-3 whitespace-pre-wrap", LEVEL[e.level])}>
                <span className="tabular text-muted-foreground shrink-0">{clock(e.time)}</span>
                <span className="w-10 shrink-0 uppercase">{e.level}</span>
                <span className="text-muted-foreground w-9 shrink-0">{e.stage}</span>
                <span className="min-w-0 break-words">{e.message}</span>
              </div>
            ))}
          </div>
        </div>
      </section>
    </div>
  )
}
