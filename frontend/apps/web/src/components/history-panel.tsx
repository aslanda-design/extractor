import { useCallback, useEffect, useMemo, useState } from "react"
import { FileText, Trash2, X } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { cn } from "@workspace/ui/lib/utils"
import { api } from "@/lib/api"
import type { HistoryRow, UploadedDocument } from "@/lib/types"

interface Props {
  open: boolean
  onClose: () => void
  /** bumps after a run finishes so the lists reload */
  refreshKey: number
  activeRunId: string | null
  activeDocument: string | null
  onOpenRun: (id: string) => void
  onOpenDocument: (name: string) => void
}

function day(iso: string) {
  const d = new Date(iso)
  const today = new Date()
  const yesterday = new Date(today.getTime() - 86_400_000)
  if (d.toDateString() === today.toDateString()) return "Today"
  if (d.toDateString() === yesterday.toDateString()) return "Yesterday"
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" })
}
const time = (iso: string) => new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })
const kb = (n: number) => (n > 1_000_000 ? `${(n / 1_000_000).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1000))} KB`)

/** Every earlier extraction and every uploaded document. Kept on disk by the engine, so it is still here after a restart. */
export function HistoryPanel({ open, onClose, refreshKey, activeRunId, activeDocument, onOpenRun, onOpenDocument }: Props) {
  const [tab, setTab] = useState<"runs" | "documents">("runs")
  const [runs, setRuns] = useState<HistoryRow[] | null>(null)
  const [docs, setDocs] = useState<UploadedDocument[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [confirm, setConfirm] = useState<string | null>(null)

  const load = useCallback(() => {
    Promise.all([api.runs(), api.documents()]).then(
      ([r, d]) => {
        setRuns(r)
        setDocs(d)
        setError(null)
      },
      (e: Error) => setError(e.message)
    )
  }, [])

  useEffect(() => {
    if (open) load()
  }, [open, refreshKey, load])

  useEffect(() => {
    if (!open) return
    const on = (e: KeyboardEvent) => e.key === "Escape" && onClose()
    addEventListener("keydown", on)
    return () => removeEventListener("keydown", on)
  }, [open, onClose])

  const grouped = useMemo(() => {
    const out: [string, HistoryRow[]][] = []
    for (const r of runs ?? []) {
      const d = day(r.started_at)
      if (out.at(-1)?.[0] === d) out.at(-1)![1].push(r)
      else out.push([d, [r]])
    }
    return out
  }, [runs])

  const runsByDocument = useMemo(() => {
    const m = new Map<string, number>()
    for (const r of runs ?? []) for (const d of r.params.documents ?? []) m.set(d, (m.get(d) ?? 0) + 1)
    return m
  }, [runs])

  const remove = (id: string) => {
    if (confirm !== id) return setConfirm(id)
    setConfirm(null)
    void api.deleteRun(id).then(load, (e: Error) => setError(e.message))
  }

  return (
    <aside
      aria-label="History"
      aria-hidden={!open}
      inert={!open}
      className={cn(
        "bg-card absolute inset-y-0 left-0 z-30 flex w-[min(22rem,100%)] flex-col border-r shadow-[10px_0_28px_-14px_rgb(0_0_0/0.45)] transition-transform duration-300 ease-[cubic-bezier(0.16,1,0.3,1)]",
        open ? "translate-x-0" : "-translate-x-full"
      )}
    >
      <div className="flex h-11 shrink-0 items-center gap-1 border-b px-2">
        {(["runs", "documents"] as const).map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => setTab(t)}
            aria-pressed={tab === t}
            className={cn("rounded-md px-3 py-1.5 text-sm transition-colors", tab === t ? "bg-secondary font-medium" : "text-muted-foreground hover:text-foreground")}
          >
            {t === "runs" ? `Runs${runs ? ` (${runs.length})` : ""}` : `Documents${docs ? ` (${docs.length})` : ""}`}
          </button>
        ))}
        <Button variant="ghost" size="icon-sm" className="ml-auto" onClick={onClose} aria-label="Close history">
          <X aria-hidden />
        </Button>
      </div>
      {error && <p role="alert" className="text-bad px-3 py-2 text-xs">{error}</p>}
      <div className="min-h-0 flex-1 overflow-auto">
        {tab === "runs" && (
          <>
            {runs && runs.length === 0 && <p className="text-muted-foreground p-4 text-sm">No saved runs yet. Every extraction you run is kept here, even after you close the app.</p>}
            {grouped.map(([label, rows]) => (
              <section key={label}>
                <h3 className="bg-card text-muted-foreground sticky top-0 border-b px-3 py-1.5 text-xs font-medium">{label}</h3>
                <ul>
                  {rows.map((r) => {
                    const doc = r.params.documents?.[0] ?? "document"
                    const active = r.run_id === activeRunId
                    return (
                      <li key={r.run_id} className={cn("group/row flex items-stretch border-b", active && "bg-evidence-soft")}>
                        <button type="button" onClick={() => onOpenRun(r.run_id)} className="hover:bg-secondary min-w-0 flex-1 px-3 py-2.5 text-left transition-colors">
                          <span className="flex items-center gap-2">
                            <span className="size-2 shrink-0 rounded-full" style={{ background: r.status === "ok" ? "var(--ok)" : "var(--bad)" }} aria-hidden />
                            <span className="min-w-0 flex-1 truncate text-sm font-medium">{doc}</span>
                            <span className="tabular text-muted-foreground shrink-0 text-xs">{time(r.started_at)}</span>
                          </span>
                          <span className="text-muted-foreground mt-0.5 block truncate text-xs">{r.headline || r.error || "No summary"}</span>
                          <span className="text-muted-foreground tabular block truncate text-[11px]">
                            {r.params.spec_name} · {r.seconds !== null ? `${r.seconds}s` : "time unknown"}
                          </span>
                        </button>
                        <button
                          type="button"
                          onClick={() => remove(r.run_id)}
                          onBlur={() => setConfirm(null)}
                          aria-label={confirm === r.run_id ? "Confirm delete this run" : "Delete this run"}
                          className={cn(
                            "text-muted-foreground hover:text-bad flex w-9 shrink-0 items-center justify-center text-xs transition-opacity",
                            confirm === r.run_id ? "text-bad w-16 opacity-100" : "opacity-0 group-hover/row:opacity-100 focus-visible:opacity-100"
                          )}
                        >
                          {confirm === r.run_id ? "Delete?" : <Trash2 className="size-4" aria-hidden />}
                        </button>
                      </li>
                    )
                  })}
                </ul>
              </section>
            ))}
          </>
        )}
        {tab === "documents" && (
          <>
            {docs && docs.length === 0 && <p className="text-muted-foreground p-4 text-sm">No uploaded documents yet.</p>}
            <ul>
              {docs?.map((d) => (
                <li key={d.name} className={cn("border-b", d.name === activeDocument && "bg-evidence-soft")}>
                  <button type="button" onClick={() => onOpenDocument(d.name)} className="hover:bg-secondary flex w-full items-start gap-2.5 px-3 py-2.5 text-left transition-colors">
                    <FileText className="text-muted-foreground mt-0.5 size-4 shrink-0" aria-hidden />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium">{d.name}</span>
                      <span className="text-muted-foreground tabular block text-xs">
                        {kb(d.size)} · {runsByDocument.get(d.name) ?? 0} {(runsByDocument.get(d.name) ?? 0) === 1 ? "run" : "runs"}
                        {d.indexed ? " · indexed" : d.parsed ? " · parsed" : ""}
                      </span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
      <p className="text-muted-foreground border-t px-3 py-2 text-[11px]">Saved as files in the engine workspace. They stay after you close the app.</p>
    </aside>
  )
}
