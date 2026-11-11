import { useEffect, useRef } from "react"
import { ChevronUp, Radio } from "lucide-react"

import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@workspace/ui/components/collapsible"
import { cn } from "@workspace/ui/lib/utils"
import type { EngineEvent } from "@/lib/types"

export type LogMode = "idle" | "live" | "recorded"

const LEVEL: Record<string, string> = {
  debug: "text-muted-foreground",
  info: "text-foreground",
  ok: "text-ok",
  warn: "text-warn",
  error: "text-bad",
}

function clock(t: number) {
  return new Date(t * 1000).toLocaleTimeString("en-GB", { hour12: false })
}

interface Props {
  events: EngineEvent[]
  mode: LogMode
  running: boolean
  open: boolean
  onOpenChange: (open: boolean) => void
  recordedNote?: string
  streamError: boolean
}

export function LogsPanel({ events, mode, running, open, onOpenChange, recordedNote, streamError }: Props) {
  const body = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = body.current
    if (el && open) el.scrollTop = el.scrollHeight
  }, [events.length, open])

  const last = events[events.length - 1]
  return (
    <Collapsible open={open} onOpenChange={onOpenChange} className="bg-card shrink-0 border-t">
      <CollapsibleTrigger className="hover:bg-secondary focus-visible:ring-ring flex h-9 w-full items-center gap-3 px-4 text-left text-sm focus-visible:ring-2 focus-visible:outline-none focus-visible:ring-inset">
        <Radio className={cn("size-4", running ? "text-primary animate-pulse" : "text-muted-foreground")} aria-hidden />
        <span className="font-medium">Engine logs</span>
        {mode === "recorded" && <span className="bg-evidence-soft text-evidence-foreground rounded-full px-2 py-0.5 text-xs">Recorded</span>}
        {mode === "live" && (
          <span className="bg-secondary rounded-full px-2 py-0.5 text-xs">{running ? "Live" : "Last run"}</span>
        )}
        <span className="text-muted-foreground min-w-0 flex-1 truncate font-mono text-xs">
          {!open && last ? `${clock(last.time)} ${last.stage} ${last.message}` : ""}
        </span>
        <span className="tabular text-muted-foreground text-xs">{events.length} lines</span>
        <ChevronUp className={cn("size-4 transition-transform", !open && "rotate-180")} aria-hidden />
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div ref={body} className="h-56 overflow-auto border-t px-4 py-2 font-mono text-xs leading-relaxed" role="log" aria-live="off">
          {mode === "recorded" && recordedNote && <p className="text-evidence-foreground mb-2 font-sans">{recordedNote}</p>}
          {events.length === 0 && (
            <p className="text-muted-foreground font-sans">
              {mode === "recorded"
                ? "No recorded logs were saved for this sample."
                : "Engine events for the current extraction will appear here as they arrive."}
            </p>
          )}
          {streamError && <p className="text-warn font-sans">The log stream was interrupted; the engine may have stopped.</p>}
          {events.map((e) => (
            <div key={e.seq} className={cn("flex gap-3 whitespace-pre-wrap", LEVEL[e.level])}>
              <span className="tabular text-muted-foreground shrink-0">{clock(e.time)}</span>
              <span className="w-10 shrink-0 uppercase">{e.level}</span>
              <span className="text-muted-foreground w-9 shrink-0">{e.stage}</span>
              <span className="min-w-0 break-words">{e.message}</span>
            </div>
          ))}
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}
