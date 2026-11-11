import { useCallback, useEffect, useMemo, useState } from "react"
import { RefreshCw, Trash2 } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { cn } from "@workspace/ui/lib/utils"
import { del, get, type HistoryRow } from "./dev-api"
import { JsonView } from "./json-view"

/** Every job the engine ran (parse, index, retrieve, extract, run, corpus measures), newest first. */
export function HistoryView() {
  const [rows, setRows] = useState<HistoryRow[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [kind, setKind] = useState("all")
  const [status, setStatus] = useState("all")
  const [text, setText] = useState("")
  const [openId, setOpenId] = useState<string | null>(null)
  const [detail, setDetail] = useState<HistoryRow | null>(null)

  const load = useCallback(() => {
    get<HistoryRow[]>("/api/history").then(setRows, (e: Error) => setError(e.message))
  }, [])
  useEffect(() => {
    // initial load; state is set after the fetch resolves
    load()
  }, [load])

  const kinds = useMemo(() => ["all", ...new Set((rows ?? []).map((r) => r.kind))], [rows])
  const shown = useMemo(
    () =>
      (rows ?? []).filter(
        (r) =>
          (kind === "all" || r.kind === kind) &&
          (status === "all" || r.status === status) &&
          (!text || `${r.subject} ${r.headline} ${r.label}`.toLowerCase().includes(text.toLowerCase()))
      ),
    [rows, kind, status, text]
  )

  const open = (id: string) => {
    setOpenId(id)
    setDetail(null)
    get<HistoryRow>(`/api/history/${encodeURIComponent(id)}`).then(setDetail, (e: Error) => setError(e.message))
  }

  const sel = "bg-card h-8 rounded-md border px-2 text-xs"
  return (
    <div className="grid h-full min-h-0 lg:grid-cols-[26rem_1fr]">
      <div className="flex min-h-0 flex-col border-b lg:border-r lg:border-b-0">
        <div className="bg-card flex flex-wrap items-center gap-2 border-b px-3 py-2">
          <select className={sel} value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Kind">
            {kinds.map((k) => (
              <option key={k}>{k}</option>
            ))}
          </select>
          <select className={sel} value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status">
            {["all", "ok", "error"].map((k) => (
              <option key={k}>{k}</option>
            ))}
          </select>
          <input className={cn(sel, "min-w-24 flex-1")} placeholder="Filter" value={text} onChange={(e) => setText(e.target.value)} aria-label="Filter history" />
          <Button variant="ghost" size="icon-sm" onClick={load} aria-label="Refresh">
            <RefreshCw aria-hidden />
          </Button>
        </div>
        {error && <p className="text-bad px-3 py-2 text-xs">{error}</p>}
        <ul className="min-h-0 flex-1 overflow-auto">
          {rows && shown.length === 0 && <li className="text-muted-foreground p-3 text-sm">No runs yet.</li>}
          {shown.map((r) => (
            <li key={r.run_id}>
              <button
                type="button"
                onClick={() => open(r.run_id)}
                className={cn("hover:bg-secondary w-full border-b px-3 py-2 text-left", openId === r.run_id && "bg-secondary")}
              >
                <div className="flex items-center gap-2 text-xs">
                  <span className={cn("size-2 rounded-full", r.status === "ok" ? "bg-ok" : "bg-bad")} aria-hidden />
                  <span className="font-medium">{r.label}</span>
                  <span className="text-muted-foreground tabular ml-auto">{r.seconds}s</span>
                </div>
                <p className="mt-0.5 truncate text-xs">{r.subject}</p>
                <p className="text-muted-foreground truncate text-xs">{r.headline}</p>
                <p className="text-muted-foreground tabular text-[11px]">{r.started_at.replace("T", " ")}</p>
              </button>
            </li>
          ))}
        </ul>
      </div>
      <div className="min-h-0 space-y-3 overflow-auto p-4">
        {!openId && <p className="text-muted-foreground text-sm">Pick a run to see its parameters, the models it used and its full result.</p>}
        {openId && !detail && <p className="text-muted-foreground text-sm">Loading...</p>}
        {detail && (
          <>
            <div className="flex items-start gap-2">
              <div className="min-w-0 flex-1">
                <h2 className="text-sm font-semibold">
                  {detail.label} <span className="text-muted-foreground font-normal">· {detail.run_id}</span>
                </h2>
                <p className="text-muted-foreground text-xs">
                  {Object.entries(detail.settings ?? {})
                    .map(([k, v]) => `${k}: ${v}`)
                    .join(" · ")}
                </p>
              </div>
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  void del(`/api/history/${encodeURIComponent(detail.run_id)}`).then(() => {
                    setOpenId(null)
                    setDetail(null)
                    load()
                  })
                }}
              >
                <Trash2 aria-hidden /> Delete
              </Button>
            </div>
            {detail.error && <p className="bg-bad-soft text-bad rounded-md px-3 py-2 text-sm">{detail.error}</p>}
            <h3 className="text-xs font-semibold uppercase tracking-wide">Parameters</h3>
            <JsonView value={detail.params} />
            <h3 className="text-xs font-semibold uppercase tracking-wide">Result</h3>
            <JsonView value={detail.result ?? null} />
          </>
        )}
      </div>
    </div>
  )
}
