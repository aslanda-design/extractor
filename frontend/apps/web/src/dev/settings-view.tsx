import { useCallback, useEffect, useState } from "react"

import { Button } from "@workspace/ui/components/button"
import { Textarea } from "@workspace/ui/components/textarea"
import { del, get, put } from "./dev-api"
import { JsonView } from "./json-view"

/** The runtime configuration (models, parsing, retrieval, recovery): view, edit as JSON, reset. */
export function SettingsView() {
  const [text, setText] = useState("")
  const [options, setOptions] = useState<unknown>(null)
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)

  const load = useCallback(() => {
    get<unknown>("/api/config").then(
      (c) => setText(JSON.stringify(c, null, 2)),
      (e: Error) => setMessage({ ok: false, text: e.message })
    )
  }, [])
  useEffect(() => {
    // initial load; state is set after the fetches resolve
    load()
    get<unknown>("/api/options").then(setOptions, () => {})
  }, [load])

  const save = () => {
    let data: unknown
    try {
      data = JSON.parse(text)
    } catch (e) {
      setMessage({ ok: false, text: `Not valid JSON: ${(e as Error).message}` })
      return
    }
    put<unknown>("/api/config", data).then(
      (c) => {
        setText(JSON.stringify(c, null, 2))
        setMessage({ ok: true, text: "Saved. Re-parse or re-chunk documents to apply parsing and chunking changes." })
      },
      (e: Error) => setMessage({ ok: false, text: e.message })
    )
  }

  const reset = () => {
    del<unknown>("/api/config").then(
      (c) => {
        setText(JSON.stringify(c, null, 2))
        setMessage({ ok: true, text: "Reset to defaults." })
      },
      (e: Error) => setMessage({ ok: false, text: e.message })
    )
  }

  return (
    <div className="grid h-full min-h-0 lg:grid-cols-[1fr_24rem]">
      <div className="flex min-h-0 flex-col gap-2 p-4">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold">Runtime configuration</h2>
          <span className="text-muted-foreground text-xs">Which models and settings. What to extract lives in the spec, not here.</span>
        </div>
        <Textarea className="min-h-0 flex-1 resize-none font-mono text-xs" value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} aria-label="Runtime configuration JSON" />
        <div className="flex items-center gap-2">
          <Button size="sm" onClick={save}>
            Save
          </Button>
          <Button size="sm" variant="outline" onClick={load}>
            Revert edits
          </Button>
          <Button size="sm" variant="outline" onClick={reset}>
            Reset to defaults
          </Button>
          {message && <span className={message.ok ? "text-ok text-xs" : "text-bad text-xs"}>{message.text}</span>}
        </div>
      </div>
      <aside className="bg-card min-h-0 space-y-2 overflow-auto border-t p-4 lg:border-t-0 lg:border-l">
        <h2 className="text-sm font-semibold">Registered options</h2>
        <p className="text-muted-foreground text-xs">Adapters, stores and profiles the engine offers.</p>
        {options !== null && <JsonView value={options} />}
      </aside>
    </div>
  )
}
