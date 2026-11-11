import { useState } from "react"
import { Check, Loader2, PencilLine, UserCheck, X } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { cn } from "@workspace/ui/lib/utils"
import { formatValue } from "@/lib/review"
import type { ReviewedItem, Status } from "@/lib/types"

interface Props {
  taskId: string
  status: Status
  /** what the reviewers decided about this task, if anything (the engine's answer is never replaced: it is shown beside it) */
  reviewed?: ReviewedItem
  disabled?: boolean
  onReview: (decision: "accept" | "correct" | "reject", value?: string, note?: string) => Promise<void>
}

const DECISION: Record<string, string> = { accept: "Accepted by a reviewer", correct: "Corrected by a reviewer", reject: "Marked as not in the document" }

/** Accept, correct or reject one answer. The decision is kept apart from the engine's answer (S13). */
export function ReviewBar({ taskId, status, reviewed, disabled, onReview }: Props) {
  const [correcting, setCorrecting] = useState(false)
  const [value, setValue] = useState("")
  const [note, setNote] = useState("")
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const canAccept = status === "supported" || status === "not_found"
  const send = async (decision: "accept" | "correct" | "reject") => {
    setBusy(decision)
    setError(null)
    try {
      await onReview(decision, decision === "correct" ? value : undefined, note || undefined)
      setCorrecting(false)
      setValue("")
      setNote("")
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(null)
    }
  }

  return (
    <section aria-label={`Review ${taskId}`}>
      <h3 className="text-muted-foreground mb-1.5 flex items-center gap-1.5 text-xs font-semibold tracking-wide uppercase">
        <UserCheck className="size-3.5" aria-hidden /> Review
      </h3>
      {reviewed?.source === "human" && (
        <p className="bg-ok-soft text-ok mb-2 rounded-md px-3 py-2">
          {DECISION[reviewed.decision ?? "accept"]}
          {reviewed.decision === "correct" && <>: <b>{formatValue(reviewed.value)}</b></>}
          <span className="text-muted-foreground block text-xs">
            The engine said {reviewed.engine_status.replace("_", " ")}
            {reviewed.engine_value != null ? `: ${formatValue(reviewed.engine_value)}` : ""}. Both are kept.
            {reviewed.note ? ` Note: ${reviewed.note}` : ""}
          </span>
        </p>
      )}
      {reviewed?.stale && <p className="bg-warn-soft text-warn mb-2 rounded-md px-3 py-2 text-xs">{reviewed.note}</p>}
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="outline" disabled={disabled || !canAccept || busy !== null} onClick={() => void send("accept")}
          title={canAccept ? "The engine's answer is right" : "The engine did not stand behind an answer: correct it or reject it"}>
          {busy === "accept" ? <Loader2 className="animate-spin" aria-hidden /> : <Check aria-hidden />} Accept
        </Button>
        <Button size="sm" variant="outline" disabled={disabled || busy !== null} onClick={() => setCorrecting(!correcting)} aria-expanded={correcting}>
          <PencilLine aria-hidden /> Correct
        </Button>
        <Button size="sm" variant="outline" disabled={disabled || busy !== null} onClick={() => void send("reject")}
          title="The document gives no answer for this">
          {busy === "reject" ? <Loader2 className="animate-spin" aria-hidden /> : <X aria-hidden />} Not in the document
        </Button>
      </div>
      {correcting && (
        <form
          className="mt-2 space-y-2"
          onSubmit={(e) => {
            e.preventDefault()
            void send("correct")
          }}
        >
          <label className="block text-xs">
            The right value
            <input className="bg-background mt-1 block w-full rounded-md border px-2 py-1.5 text-sm" value={value} onChange={(e) => setValue(e.target.value)} required />
          </label>
          <label className="block text-xs">
            Note (optional)
            <input className="bg-background mt-1 block w-full rounded-md border px-2 py-1.5 text-sm" value={note} onChange={(e) => setNote(e.target.value)} />
          </label>
          <Button size="sm" type="submit" disabled={busy !== null || !value.trim()}>
            {busy === "correct" ? <Loader2 className="animate-spin" aria-hidden /> : null} Save correction
          </Button>
        </form>
      )}
      {error && <p role="alert" className={cn("bg-bad-soft text-bad mt-2 rounded-md px-3 py-2 text-xs")}>{error}</p>}
      <p className="text-muted-foreground mt-2 text-xs">Reviews are saved apart from the run. Lists can be reviewed record by record with the command line or the API.</p>
    </section>
  )
}
