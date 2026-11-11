import { useEffect, useRef, useState } from "react"
import { FileUp, Loader2, Play, Sparkles } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@workspace/ui/components/dialog"
import { Textarea } from "@workspace/ui/components/textarea"
import { api } from "@/lib/api"
import type { SpecDraft, UploadedDocument } from "@/lib/types"

export const DEFAULT_SPEC = `version: 1
name: single value
tasks:
- id: agreement_date
  kind: value
  description: The date on which the agreement is entered into.
  output: {type: date, format: ISO-8601}
  rules:
    normalization: Convert to YYYY-MM-DD.
    on_absent: not_found
`

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
  running: boolean
  /** called once the file and spec are saved on the engine; the app then starts the run */
  onStart: (args: { documentName: string; specName: string; specText: string; fileName: string }) => void
  /** an already uploaded document to preselect (from the history) */
  initialDocument?: string
  /** the YAML to start from (from a past run); the default one-value spec otherwise */
  initialSpec?: string
  /** "edit" re-runs the current extraction with a changed YAML */
  mode?: "new" | "edit"
}

export function RunDialog({ open, onOpenChange, running, onStart, initialDocument, initialSpec, mode = "new" }: Props) {
  const input = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [spec, setSpec] = useState(initialSpec?.trim() ? initialSpec : DEFAULT_SPEC)
  const [existing, setExisting] = useState(initialDocument ?? "")
  const [uploaded, setUploaded] = useState<UploadedDocument[]>([])
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [ask, setAsk] = useState("")
  const [drafting, setDrafting] = useState(false)
  const [draft, setDraft] = useState<SpecDraft | null>(null)
  const [draftError, setDraftError] = useState<string | null>(null)
  // with the default spec in the editor the request writes a new spec; with anything else it edits that one
  const refining = spec.trim() !== DEFAULT_SPEC.trim() && spec.trim() !== ""

  useEffect(() => {
    void api.documents().then(setUploaded, () => {})
  }, [])

  const generate = async () => {
    if (!ask.trim()) return
    setDrafting(true)
    setDraftError(null)
    try {
      const out = await api.draftSpec(ask, refining ? spec : undefined)
      setDraft(out)
      if (out.yaml) setSpec(out.yaml)
      if (out.valid) setAsk("")
    } catch (e) {
      setDraft(null)
      setDraftError((e as Error).message)
    } finally {
      setDrafting(false)
    }
  }

  const submit = async () => {
    if (!file && !existing) return setError("Choose a PDF or image first.")
    setBusy(true)
    setError(null)
    try {
      const check = await api.validateSpec(spec)
      if (!check.ok) throw new Error(check.errors?.map((e) => e.message).join("; ") || "The YAML is not a valid specification.")
      const documentName = file ? (await api.upload(file))[0] : existing
      // one spec file per extraction (kept in the workspace next to the run), never overwritten
      const stamp = new Date().toISOString().replace(/[-:T]/g, "").slice(0, 14)
      const { name: specName } = await api.saveSpec(`extraction-${stamp}.yaml`, spec)
      onStart({ documentName, specName, specText: spec, fileName: file?.name ?? existing })
      onOpenChange(false)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92svh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{mode === "edit" ? "Edit the YAML and re-run" : "New extraction"}</DialogTitle>
          <DialogDescription>
            {mode === "edit"
              ? "Change what to extract by hand or ask the assistant, then run the same document again. The earlier run stays in the history with its own YAML."
              : "Upload a document and describe what to extract. The engine reads the document, finds evidence for each field and checks every answer."}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div>
            <span className="mb-1.5 block text-sm font-medium">Document</span>
            <input
              ref={input}
              type="file"
              accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff"
              className="sr-only"
              aria-label="Document file"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
            <Button type="button" variant="outline" className="w-full justify-start" onClick={() => input.current?.click()}>
              <FileUp aria-hidden />
              {file ? file.name : "Choose a PDF or image…"}
            </Button>
            {uploaded.length > 0 && (
              <select
                className="bg-background mt-2 h-8 w-full rounded-md border px-2 text-sm"
                value={file ? "" : existing}
                onChange={(e) => {
                  setExisting(e.target.value)
                  setFile(null)
                }}
                aria-label="Or use an uploaded document"
              >
                <option value="">Or use a document you uploaded before</option>
                {uploaded.map((d) => (
                  <option key={d.name} value={d.name}>
                    {d.name}
                  </option>
                ))}
              </select>
            )}
          </div>
          <div className="bg-muted/40 rounded-lg border p-3">
            <label htmlFor="ask" className="mb-1.5 flex items-center gap-1.5 text-sm font-medium">
              <Sparkles className="text-primary size-4" aria-hidden />
              Describe it, let the assistant write the YAML
            </label>
            <Textarea
              id="ask"
              value={ask}
              onChange={(e) => setAsk(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) void generate()
              }}
              placeholder={
                refining
                  ? "Say what to change, e.g. “also extract the due date and make the total an amount”"
                  : "e.g. the contract’s start and end dates, whether it renews automatically, and every payment obligation"
              }
              className="h-20 text-sm"
              disabled={drafting}
            />
            <div className="mt-2 flex items-center justify-between gap-3">
              <p className="text-muted-foreground text-xs">
                {refining ? "Edits the YAML below." : "Writes a new YAML."} You can still edit it by hand. Ctrl+Enter to send.
              </p>
              <Button type="button" size="sm" onClick={generate} disabled={drafting || !ask.trim()}>
                {drafting ? <Loader2 className="animate-spin" aria-hidden /> : <Sparkles aria-hidden />}
                {drafting ? "Writing…" : refining ? "Update YAML" : "Generate YAML"}
              </Button>
            </div>
            {draftError && (
              <p role="alert" className="text-destructive mt-2 text-sm">
                {draftError}
              </p>
            )}
            {draft && (
              <div className="mt-2 space-y-1 text-xs" aria-live="polite">
                <p className={draft.valid ? "text-evidence-foreground" : "text-destructive"}>
                  {draft.valid ? "The YAML is valid and ready to run." : "The YAML still has errors; fix them by hand or ask again."} ({draft.seconds}s,{" "}
                  {draft.calls} model call{draft.calls === 1 ? "" : "s"})
                </p>
                {draft.errors.map((e, i) => (
                  <p key={i} className="text-destructive">
                    {e.path ? `${e.path}: ` : ""}
                    {e.message}
                  </p>
                ))}
                {draft.warnings.map((w, i) => (
                  <p key={i} className="text-muted-foreground">
                    Check: {w}
                  </p>
                ))}
              </div>
            )}
          </div>
          <div>
            <label htmlFor="spec" className="mb-1.5 block text-sm font-medium">
              What to extract (YAML)
            </label>
            <Textarea
              id="spec"
              value={spec}
              onChange={(e) => {
                setSpec(e.target.value)
                setDraft(null)
              }}
              spellCheck={false}
              className="h-56 font-mono text-xs"
            />
            <p className="text-muted-foreground mt-1 text-xs">Fewer fields run faster. The default asks for one value.</p>
          </div>
          {error && (
            <p role="alert" className="text-destructive text-sm">
              {error}
            </p>
          )}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={busy || running}>
            {busy ? <Loader2 className="animate-spin" aria-hidden /> : <Play aria-hidden />}
            {busy ? "Saving…" : mode === "edit" ? "Re-run extraction" : "Run extraction"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
