import { useEffect, useRef, useState } from "react"
import { Loader2, Play, RotateCcw, Sparkles, Square } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { cn } from "@workspace/ui/lib/utils"
import type { EngineEvent } from "@/lib/types"
import { demoScript } from "@/pipeline/demo"
import { FieldList, PipelineStage } from "@/pipeline/panels"
import { usePipelineFeed } from "@/pipeline/use-feed"
import { get, openEventStream, post } from "./dev-api"

type Source = "live" | "replay" | "demo" | null

const SOURCE_NOTE: Record<Exclude<Source, null>, { label: string; tone: string }> = {
  live: { label: "Live engine run", tone: "bg-ok-soft text-ok" },
  replay: { label: "Replay of the last real run", tone: "bg-secondary text-foreground" },
  demo: { label: "Simulated demo, not engine output", tone: "bg-evidence-soft text-evidence-foreground" },
}

interface Doc {
  name: string
  size: number
}

/** The engine as a living diagram (developer view): choose a spec and a document, or replay and simulate. */
export function PipelineView() {
  const feed = usePipelineFeed()
  const [source, setSource] = useState<Source>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [hoverId, setHoverId] = useState<string | null>(null)
  const [specs, setSpecs] = useState<string[]>([])
  const [docs, setDocs] = useState<Doc[]>([])
  const [spec, setSpec] = useState("")
  const [doc, setDoc] = useState("")
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const recorded = useRef<EngineEvent[]>([])
  const [hasRecording, setHasRecording] = useState(false)

  useEffect(() => {
    void Promise.all([get<string[]>("/api/specs"), get<Doc[]>("/api/documents")]).then(
      ([s, d]) => {
        setSpecs(s)
        setDocs(d)
      },
      (e: Error) => setError(e.message)
    )
  }, [])

  const begin = (s: Source) => {
    feed.reset()
    setSelectedId(null)
    setError(null)
    setSource(s)
  }

  const busy = running || feed.replaying

  const runDemo = () => {
    begin("demo")
    feed.play(demoScript().map((b) => ({ at: b.at, e: b.ev as never })))
  }

  const replay = () => {
    begin("replay")
    feed.replay(recorded.current)
  }

  const startLive = async () => {
    begin("live")
    recorded.current = []
    setHasRecording(false)
    setRunning(true)
    const runs = new Set<string>()
    let close = () => {}
    let sawTrace = false
    try {
      const since = (await get<{ last: number }>("/api/logs?since=2147483647")).last
      close = openEventStream(
        since,
        (e) => {
          if (e.kind === "run_start") runs.add(e.run)
          if (!runs.has(e.run)) return
          if (e.kind === "trace") sawTrace = true
          recorded.current.push(e)
          feed.push(e)
        },
        () => {}
      )
      await post("/api/run", { spec, documents: [doc] })
      if (!sawTrace) setError("The run finished but the engine sent no pipeline events. Restart the engine (npm run dev) so it loads the new tracing, then run again.")
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setRunning(false)
      setHasRecording(sawTrace)
      setTimeout(close, 1200)
    }
  }

  const sel = "bg-background h-8 rounded-md border px-2 text-sm"
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="bg-card flex flex-wrap items-center gap-2 border-b px-3 py-2">
        <select className={cn(sel, "max-w-44")} value={spec} onChange={(e) => setSpec(e.target.value)} aria-label="Specification">
          <option value="">Spec</option>
          {specs.map((s) => (
            <option key={s}>{s}</option>
          ))}
        </select>
        <select className={cn(sel, "max-w-52")} value={doc} onChange={(e) => setDoc(e.target.value)} aria-label="Document">
          <option value="">Document</option>
          {docs.map((d) => (
            <option key={d.name}>{d.name}</option>
          ))}
        </select>
        <Button size="sm" disabled={busy || !spec || !doc} onClick={() => void startLive()}>
          {running ? <Loader2 className="animate-spin" aria-hidden /> : <Play aria-hidden />}
          Run on the engine
        </Button>
        <Button size="sm" variant="outline" disabled={busy || !hasRecording} onClick={replay}>
          <RotateCcw aria-hidden /> Replay last run
        </Button>
        <Button size="sm" variant="outline" disabled={busy} onClick={runDemo}>
          <Sparkles aria-hidden /> Simulated demo
        </Button>
        {feed.replaying && (
          <Button size="sm" variant="ghost" onClick={feed.stop}>
            <Square aria-hidden /> Stop
          </Button>
        )}
        {source && <span className={cn("rounded-full px-2.5 py-1 text-xs font-medium", SOURCE_NOTE[source].tone)}>{SOURCE_NOTE[source].label}</span>}
        {error && (
          <span role="alert" className="text-bad min-w-0 flex-1 text-xs">
            {error}
          </span>
        )}
      </div>

      <div className="grid min-h-0 flex-1 gap-3 p-3 lg:grid-cols-[minmax(0,1fr)_21rem]">
        <PipelineStage dark state={feed.state} selectedId={selectedId} hoverId={hoverId} onSelect={setSelectedId} className="min-h-[26rem]" />
        <FieldList className="bg-card overflow-hidden rounded-xl border" state={feed.state} selectedId={selectedId} onSelect={setSelectedId} onHover={setHoverId} />
      </div>
    </div>
  )
}
