import { useEffect, useMemo, useRef, useState } from "react"
import { Pause, Play, Trash2 } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { cn } from "@workspace/ui/lib/utils"
import { del, openEventStream, type DevEvent } from "./dev-api"

const LEVEL: Record<string, string> = { debug: "text-muted-foreground", info: "text-foreground", ok: "text-ok", warn: "text-warn", error: "text-bad" }
const LEVELS = ["all", "info", "ok", "warn", "error", "debug"] as const

export const clock = (t: number) => new Date(t * 1000).toLocaleTimeString("en-GB", { hour12: false })

/** Every engine event the server holds, then live. The same stream as the launch terminal's [engine] lines. */
export function LogsView() {
  const [events, setEvents] = useState<DevEvent[]>([])
  const [connected, setConnected] = useState(false)
  const [paused, setPaused] = useState(false)
  const [level, setLevel] = useState<(typeof LEVELS)[number]>("all")
  const [stage, setStage] = useState("all")
  const [run, setRun] = useState("all")
  const [text, setText] = useState("")
  const [epoch, setEpoch] = useState(0)
  const buffer = useRef<DevEvent[]>([])
  const pausedRef = useRef(false)
  const body = useRef<HTMLDivElement>(null)

  useEffect(() => {
    pausedRef.current = paused
    if (!paused) setEvents([...buffer.current])
  }, [paused])

  useEffect(() => {
    buffer.current = []
    return openEventStream(
      0,
      (e) => {
        buffer.current.push(e)
        if (buffer.current.length > 5000) buffer.current.shift()
        if (!pausedRef.current) setEvents([...buffer.current])
      },
      setConnected
    )
  }, [epoch])

  const stages = useMemo(() => ["all", ...new Set(events.map((e) => e.stage))], [events])
  const runs = useMemo(() => ["all", ...new Set(events.map((e) => e.run).filter(Boolean))], [events])
  const shown = useMemo(
    () =>
      events.filter(
        (e) =>
          (level === "all" || e.level === level) &&
          (stage === "all" || e.stage === stage) &&
          (run === "all" || e.run === run) &&
          (!text || e.message.toLowerCase().includes(text.toLowerCase()))
      ),
    [events, level, stage, run, text]
  )

  useEffect(() => {
    const el = body.current
    if (el && !paused) el.scrollTop = el.scrollHeight
  }, [shown.length, paused])

  const sel = "bg-card h-8 rounded-md border px-2 text-xs"
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="bg-card flex flex-wrap items-center gap-2 border-b px-3 py-2">
        <span className={cn("size-2 rounded-full", connected ? "bg-ok" : "bg-bad")} aria-hidden />
        <span className="text-muted-foreground text-xs">{connected ? "Connected" : "Disconnected"}</span>
        <select className={sel} value={level} onChange={(e) => setLevel(e.target.value as typeof level)} aria-label="Level">
          {LEVELS.map((l) => (
            <option key={l}>{l}</option>
          ))}
        </select>
        <select className={sel} value={stage} onChange={(e) => setStage(e.target.value)} aria-label="Stage">
          {stages.map((l) => (
            <option key={l}>{l}</option>
          ))}
        </select>
        <select className={cn(sel, "max-w-48")} value={run} onChange={(e) => setRun(e.target.value)} aria-label="Run">
          {runs.map((l) => (
            <option key={l}>{l}</option>
          ))}
        </select>
        <input className={cn(sel, "min-w-32 flex-1")} placeholder="Filter text" value={text} onChange={(e) => setText(e.target.value)} aria-label="Filter text" />
        <span className="tabular text-muted-foreground text-xs">
          {shown.length}/{events.length}
        </span>
        <Button variant="outline" size="sm" onClick={() => setPaused(!paused)}>
          {paused ? <Play aria-hidden /> : <Pause aria-hidden />}
          {paused ? "Resume" : "Pause"}
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            void del("/api/logs").then(() => {
              setEvents([])
              setEpoch((n) => n + 1)
            })
          }}
        >
          <Trash2 aria-hidden /> Clear
        </Button>
      </div>
      <div ref={body} className="min-h-0 flex-1 overflow-auto px-3 py-2 font-mono text-xs leading-relaxed" role="log">
        {shown.length === 0 && <p className="text-muted-foreground font-sans">No engine events yet. Start a run or a corpus measure and they appear here.</p>}
        {shown.map((e) => (
          <div key={e.seq} className={cn("flex gap-3 whitespace-pre-wrap", LEVEL[e.level])}>
            <span className="tabular text-muted-foreground shrink-0">{clock(e.time)}</span>
            <span className="w-10 shrink-0 uppercase">{e.level}</span>
            <span className="text-muted-foreground w-9 shrink-0">{e.stage}</span>
            <span className="min-w-0 break-words">{e.message}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
