import { useEffect, useState } from "react"
import { ArrowLeft } from "lucide-react"

import { buttonVariants } from "@workspace/ui/components/button"
import { cn } from "@workspace/ui/lib/utils"
import { ThemeToggle } from "@/lib/theme"
import { CorpusView } from "./corpus-view"
import { HistoryView } from "./history-view"
import { LogsView } from "./logs-view"
import { PipelineView } from "./pipeline-view"
import { RunView } from "./run-view"
import { SettingsView } from "./settings-view"

const TABS = [
  { id: "pipeline", label: "Pipeline", View: PipelineView },
  { id: "logs", label: "Logs", View: LogsView },
  { id: "run", label: "Run", View: RunView },
  { id: "corpus", label: "Corpus", View: CorpusView },
  { id: "history", label: "History", View: HistoryView },
  { id: "settings", label: "Settings", View: SettingsView },
] as const

type TabId = (typeof TABS)[number]["id"]

function tabFromHash(): TabId {
  const id = location.hash.replace("#/dev", "").replace(/^\//, "")
  return TABS.find((t) => t.id === id)?.id ?? "pipeline"
}

/** The engine test bench at `#/dev`: logs, runs, corpus measures, history and settings. */
export function DevApp() {
  const [tab, setTab] = useState<TabId>(tabFromHash)
  useEffect(() => {
    const on = () => setTab(tabFromHash())
    addEventListener("hashchange", on)
    return () => removeEventListener("hashchange", on)
  }, [])
  const View = TABS.find((t) => t.id === tab)!.View

  return (
    <div className="flex h-svh min-h-0 flex-col">
      <header className="bg-card flex h-12 shrink-0 items-center gap-2 border-b px-3 sm:px-4">
        <a href="#/" className={buttonVariants({ variant: "ghost", size: "sm" })} aria-label="Back to review">
          <ArrowLeft aria-hidden />
          <span className="hidden sm:inline">Review</span>
        </a>
        <span className="bg-border h-5 w-px" aria-hidden />
        <h1 className="text-sm font-semibold tracking-tight">
          <span className="text-primary">Extractor</span> test engine
        </h1>
        <nav className="ml-2 flex min-w-0 flex-1 gap-0.5 overflow-x-auto" aria-label="Engine tools">
          {TABS.map((t) => (
            <a
              key={t.id}
              href={`#/dev/${t.id}`}
              aria-current={tab === t.id ? "page" : undefined}
              className={cn(
                "rounded-md px-3 py-1.5 text-sm whitespace-nowrap transition-colors",
                tab === t.id ? "bg-secondary text-foreground font-medium" : "text-muted-foreground hover:bg-muted hover:text-foreground"
              )}
            >
              {t.label}
            </a>
          ))}
        </nav>
        <ThemeToggle />
      </header>
      <main className="min-h-0 flex-1">
        <View />
      </main>
    </div>
  )
}
