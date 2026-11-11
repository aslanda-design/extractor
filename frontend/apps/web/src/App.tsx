import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Braces, FileCode2, FileText, FlaskConical, History, PencilLine, Plus, RotateCcw, Terminal } from "lucide-react"

import { Button, buttonVariants } from "@workspace/ui/components/button"
import { Tabs, TabsList, TabsTrigger } from "@workspace/ui/components/tabs"
import { cn } from "@workspace/ui/lib/utils"
import { HistoryPanel } from "@/components/history-panel"
import { LogsPanel, type LogMode } from "@/components/logs-panel"
import { PdfViewer } from "@/components/pdf-viewer"
import { ResultsPanel } from "@/components/results-panel"
import { RunDialog } from "@/components/run-dialog"
import { TextDialog } from "@/components/text-dialog"
import { api, loadSample, streamLogs } from "@/lib/api"
import { toReviewItems } from "@/lib/review"
import { ThemeToggle } from "@/lib/theme"
import { ViewToggle, type RightView } from "@/components/view-toggle"
import { FieldList, PipelineStage } from "@/pipeline/panels"
import { usePipelineFeed } from "@/pipeline/use-feed"
import type { BlocksData, EngineEvent, Highlight, RunOutput, Source } from "@/lib/types"

const SAMPLE_NAME = "Service agreement (fictional sample)"

export function App() {
  const [source, setSource] = useState<Source | null>(null)
  const [result, setResult] = useState<RunOutput | null>(null)
  const [blocks, setBlocks] = useState<BlocksData | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [highlights, setHighlights] = useState<Highlight[]>([])
  const [jump, setJump] = useState<{ page: number; token: number } | null>(null)
  const [tab, setTab] = useState<"document" | "results">("document")
  const [error, setError] = useState<string | null>(null)

  const [events, setEvents] = useState<EngineEvent[]>([])
  const [logMode, setLogMode] = useState<LogMode>("idle")
  const [logsOpen, setLogsOpen] = useState(false)
  const [streamError, setStreamError] = useState(false)
  const [running, setRunning] = useState(false)

  const feed = usePipelineFeed()
  const [rightView, setRightView] = useState<RightView>("results")
  const [pipeSel, setPipeSel] = useState<string | null>(null)
  const [pipeHover, setPipeHover] = useState<string | null>(null)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [refreshKey, setRefreshKey] = useState(0)
  const [activeRunId, setActiveRunId] = useState<string | null>(null)
  const [dialogKey, setDialogKey] = useState(0)
  const [dialogMode, setDialogMode] = useState<"new" | "edit">("new")
  /** the engine's name of the document on screen (an upload), needed to run it again */
  const [docName, setDocName] = useState<string | null>(null)
  const [dialogInit, setDialogInit] = useState<{ document?: string; spec?: string }>({})
  const [runOpen, setRunOpen] = useState(false)
  const [view, setView] = useState<"yaml" | "json" | null>(null)
  const token = useRef(0)

  const items = useMemo(() => (result ? toReviewItems(result.results, blocks) : []), [result, blocks])
  const isSample = source?.kind === "sample"

  const openSample = useCallback(async () => {
    try {
      const s = await loadSample()
      setError(null)
      setSource({ kind: "sample", name: SAMPLE_NAME, pdfUrl: "/sample/service-agreement.pdf", yaml: s.yaml })
      setResult(s.result)
      setBlocks(s.blocks)
      setSelectedId(null)
      setHighlights([])
      setJump(null)
      setEvents(s.logs ?? [])
      setLogMode(s.logs ? "recorded" : "idle")
      setStreamError(false)
      feed.reset()
      setRightView("results")
      setActiveRunId(null)
      setDocName(null)
    } catch (e) {
      setError((e as Error).message)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    // loads the saved sample once; all state is set after the fetch resolves
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void openSample()
  }, [openSample])

  const focus = useCallback((page: number | null, hs: Highlight[], reveal?: boolean) => {
    setHighlights(hs)
    if (page !== null) setJump({ page, token: ++token.current })
    if (reveal && page !== null) setTab("document")
  }, [])

  const startRun = async (a: { documentName: string; specName: string; specText: string; fileName: string }) => {
    setError(null)
    feed.reset()
    setPipeSel(null)
    setRightView("pipeline")
    setTab("results")
    setActiveRunId(null)
    setDocName(a.documentName)
    setRunning(true)
    setResult(null)
    setBlocks(null)
    setSelectedId(null)
    setHighlights([])
    setJump(null)
    setEvents([])
    setLogMode("live")
    setStreamError(false)
    setLogsOpen(true)
    setSource({ kind: "upload", name: a.fileName, pdfUrl: api.fileUrl(a.documentName), yaml: a.specText })
    // only events of this action: those that arrive after the click and belong to a job it started
    const runs = new Set<string>()
    let stop = () => {}
    try {
      const since = await api.lastLogSeq()
      stop = streamLogs(
        since,
        (e) => {
          if (e.kind === "run_start") runs.add(e.run)
          if (runs.has(e.run)) {
            setEvents((prev) => [...prev, e])
            feed.push(e)
          }
        },
        () => setStreamError(true)
      )
      const out = await api.run(a.specName, [a.documentName])
      setResult(out)
      setActiveRunId(out.run_id ?? null)
      setBlocks(await api.blocks(a.documentName))
      setTab("results")
      setRightView("results")
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setRunning(false)
      setRefreshKey((k) => k + 1)
      setTimeout(stop, 1200) // let the last events arrive
    }
  }

  /** Reopens a saved run: its document, its answers, the spec as it was and a replayable pipeline. */
  const openRun = async (id: string) => {
    try {
      const row = await api.runDetail(id)
      const out = row.result
      const documentName = row.params.documents?.[0]
      if (!out || !documentName) throw new Error(row.error ? `That run failed: ${row.error}` : "That run has no saved result.")
      setError(null)
      let b: BlocksData | null = null
      try {
        b = await api.blocks(documentName)
      } catch {
        setError(`The document "${documentName}" is no longer in the workspace, so the page cannot be shown. The answers are still listed.`)
      }
      setSource({ kind: "upload", name: documentName, pdfUrl: api.fileUrl(documentName), yaml: out.spec_text ?? "", meta: `Run of ${new Date(row.started_at).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" })}` })
      setResult(out)
      setBlocks(b)
      setSelectedId(null)
      setHighlights([])
      setJump(null)
      setEvents([])
      setLogMode("idle")
      setActiveRunId(id)
      setDocName(documentName)
      feed.load(out.trace ?? [])
      setPipeSel(null)
      setRightView("results")
      setTab("results")
      setHistoryOpen(false)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  /** Opens an uploaded document with no answers yet, ready to run again. */
  const openDocument = (name: string) => {
    setError(null)
    setSource({ kind: "upload", name, pdfUrl: api.fileUrl(name), yaml: "" })
    setResult(null)
    setBlocks(null)
    setSelectedId(null)
    setHighlights([])
    setJump(null)
    setEvents([])
    setLogMode("idle")
    setActiveRunId(null)
    feed.reset()
    setRightView("results")
    setHistoryOpen(false)
    setDocName(name)
  }

  /** "new": a blank extraction (or the document on screen if it has no answers yet). "edit": this extraction's YAML, same document. */
  const openRunDialog = (mode: "new" | "edit") => {
    setDialogMode(mode)
    setDialogInit(mode === "edit" ? { document: docName ?? undefined, spec: source?.yaml } : { document: result ? undefined : (docName ?? undefined) })
    setDialogKey((k) => k + 1)
    setRunOpen(true)
  }

  const hasPipeline = feed.state.order.length > 0
  const toggle = <ViewToggle value={rightView} onChange={setRightView} pipelineAvailable={hasPipeline || running} />
  const json = result ? JSON.stringify(result, null, 2) : ""

  return (
    <div className="flex h-svh min-h-0 flex-col">
      <header className="bg-card flex h-12 shrink-0 items-center gap-3 border-b px-3 sm:px-4">
        <span className="text-primary hidden text-sm font-semibold tracking-tight sm:inline">Extractor</span>
        <span className="bg-border hidden h-5 w-px sm:block" aria-hidden />
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <FileText className="text-muted-foreground size-4 shrink-0" aria-hidden />
          <h1 className="truncate text-sm font-medium">{source?.name ?? "No document"}</h1>
          {isSample && (
            <span className="bg-evidence-soft text-evidence-foreground shrink-0 rounded-full px-2 py-0.5 text-xs font-medium">Sample</span>
          )}
          {source?.kind === "upload" && source.meta && <span className="text-muted-foreground hidden shrink-0 text-xs md:inline">{source.meta}</span>}
        </div>
        <Button variant={historyOpen ? "secondary" : "ghost"} size="sm" onClick={() => setHistoryOpen((o) => !o)} aria-pressed={historyOpen} aria-label="History">
          <History aria-hidden />
          <span className="hidden sm:inline">History</span>
        </Button>
        <Button variant="ghost" size="sm" disabled={!docName || running || !source?.yaml} onClick={() => openRunDialog("edit")} aria-label="Edit YAML and re-run" title="Change the YAML (by hand or with the assistant) and run this document again">
          <PencilLine aria-hidden />
          <span className="hidden lg:inline">Edit YAML</span>
        </Button>
        <Button variant="ghost" size="sm" disabled={!source} onClick={() => setView("yaml")} aria-label="View YAML">
          <FileCode2 aria-hidden />
          <span className="hidden sm:inline">View YAML</span>
        </Button>
        <Button variant="ghost" size="sm" disabled={!result} onClick={() => setView("json")} aria-label="View JSON">
          <Braces aria-hidden />
          <span className="hidden sm:inline">View JSON</span>
        </Button>
        {!isSample && (
          <Button variant="outline" size="sm" onClick={() => void openSample()} aria-label="Open sample">
            <FlaskConical aria-hidden />
            <span className="hidden md:inline">Open sample</span>
          </Button>
        )}
        <a href="#/dev" className={buttonVariants({ variant: "ghost", size: "sm" })} aria-label="Test engine">
          <Terminal aria-hidden />
          <span className="hidden md:inline">Test engine</span>
        </a>
        <ThemeToggle />
        <Button size="sm" onClick={() => openRunDialog("new")} aria-label="New extraction">
          <Plus aria-hidden />
          <span className="hidden sm:inline">New extraction</span>
        </Button>
      </header>

      <Tabs value={tab} onValueChange={(v) => setTab(v as typeof tab)} className="bg-card shrink-0 border-b px-3 py-1.5 lg:hidden">
        <TabsList className="w-full">
          <TabsTrigger value="document">Document</TabsTrigger>
          <TabsTrigger value="results">
            Results{items.length ? ` (${items.length})` : ""}
          </TabsTrigger>
        </TabsList>
      </Tabs>

      {error && (
        <p role="alert" className="bg-destructive/10 text-destructive shrink-0 border-b px-4 py-2 text-sm">
          {error}
        </p>
      )}

      <main className="relative grid min-h-0 flex-1 grid-cols-1 overflow-hidden lg:grid-cols-[3fr_2fr]">
        <HistoryPanel
          open={historyOpen}
          onClose={() => setHistoryOpen(false)}
          refreshKey={refreshKey}
          activeRunId={activeRunId}
          activeDocument={source?.kind === "upload" ? source.name : null}
          onOpenRun={(id) => void openRun(id)}
          onOpenDocument={openDocument}
        />
        <section aria-label="Document" className={cn("min-h-0 min-w-0 lg:border-r", tab !== "document" && "hidden lg:block")}>
          {source ? (
            <PdfViewer key={source.pdfUrl} url={source.pdfUrl} highlights={highlights} jump={jump} />
          ) : (
            <p className="text-muted-foreground p-6 text-sm">Open the sample or start a new extraction.</p>
          )}
        </section>
        <section aria-label="Results" className={cn("min-h-0 min-w-0", tab !== "results" && "hidden lg:block")}>
          {rightView === "pipeline" ? (
            <div className="flex h-full min-h-0 flex-col">
              <div className="bg-card flex h-11 shrink-0 items-center gap-3 border-b px-4">
                <h2 className="text-sm font-semibold">Pipeline</h2>
                <span className="text-muted-foreground text-xs">{running ? "Live from the engine" : "Recorded with this run"}</span>
                {!running && hasPipeline && (
                  <Button variant="ghost" size="xs" disabled={feed.replaying || !result?.trace?.length} onClick={() => feed.replay(result?.trace ?? [])}>
                    <RotateCcw aria-hidden /> Replay
                  </Button>
                )}
                {toggle}
              </div>
              <PipelineStage state={feed.state} selectedId={pipeSel} hoverId={pipeHover} onSelect={setPipeSel} className="min-h-0 flex-1" />
              <FieldList className="max-h-[36%] shrink-0 border-t" state={feed.state} selectedId={pipeSel} onSelect={setPipeSel} onHover={setPipeHover} />
            </div>
          ) : (
          <ResultsPanel
            trailing={toggle}
            items={items}
            selectedId={selectedId}
            onSelect={setSelectedId}
            onFocusHighlights={focus}
            running={running}
            sampleLabel={
              isSample && result?.recorded
                ? `Sample output: a real engine run saved on ${result.recorded.at} (${result.recorded.model}, ${result.recorded.seconds}s). The document is fictional.`
                : undefined
            }
          />
          )}
        </section>
      </main>

      <LogsPanel
        events={events}
        mode={logMode}
        running={running}
        open={logsOpen}
        onOpenChange={setLogsOpen}
        streamError={streamError}
        recordedNote="Recorded from the engine run that produced this sample. These are not live."
      />

      <RunDialog key={dialogKey} open={runOpen} onOpenChange={setRunOpen} running={running} onStart={(a) => void startRun(a)} initialDocument={dialogInit.document} initialSpec={dialogInit.spec} mode={dialogMode} />
      <TextDialog
        open={view === "yaml"}
        onOpenChange={(o) => !o && setView(null)}
        title="Extraction specification"
        description="The YAML that says what to extract. Model and database settings live elsewhere."
        text={source?.yaml ?? ""}
        fileName="spec.yaml"
      />
      <TextDialog
        open={view === "json"}
        onOpenChange={(o) => !o && setView(null)}
        title="Engine output"
        description={isSample ? "Saved output of a real engine run on the fictional sample." : "The engine's result for this run."}
        text={json}
        fileName="result.json"
      />
    </div>
  )
}
