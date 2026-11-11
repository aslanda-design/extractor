import type { ReactNode } from "react"
import { AlertTriangle, CheckCircle2, CircleHelp, CircleSlash, FileSearch, XCircle } from "lucide-react"

import { cn } from "@workspace/ui/lib/utils"
import { ReviewBar } from "@/components/review-bar"
import type { Highlight, ReviewItem, ReviewedItem, Status } from "@/lib/types"

const STATUS: Record<Status, { label: string; cls: string; Icon: typeof CheckCircle2 }> = {
  supported: { label: "Supported", cls: "bg-ok-soft text-ok", Icon: CheckCircle2 },
  not_found: { label: "Not found", cls: "bg-secondary text-muted-foreground", Icon: CircleSlash },
  uncertain: { label: "Uncertain", cls: "bg-warn-soft text-warn", Icon: CircleHelp },
  failed: { label: "Failed", cls: "bg-bad-soft text-bad", Icon: XCircle },
}

export function StatusBadge({ status }: { status: Status }) {
  const s = STATUS[status]
  return (
    <span className={cn("inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium", s.cls)}>
      <s.Icon className="size-3.5" aria-hidden />
      {s.label}
    </span>
  )
}

const KIND: Record<string, string> = { value: "Value", interpretation: "Interpretation", records: "List" }

interface Props {
  items: ReviewItem[]
  selectedId: string | null
  onSelect: (id: string) => void
  /** focus a list entry (or a single quote) on the page */
  onFocusHighlights: (page: number | null, hs: Highlight[], reveal?: boolean) => void
  sampleLabel?: string
  running?: boolean
  /** sits at the right end of the header (the Results / Pipeline switch) */
  trailing?: ReactNode
  /** S13: how to open the report of the run these results belong to, what reviewers decided, and how to record a decision */
  review?: { reportUrl: string; items: Record<string, ReviewedItem>; onReview: (taskId: string, decision: "accept" | "correct" | "reject", value?: string, note?: string) => Promise<void> }
}

export function ResultsPanel({ items, selectedId, onSelect, onFocusHighlights, sampleLabel, running, trailing, review }: Props) {
  const counts = items.reduce<Record<string, number>>((m, i) => ({ ...m, [i.status]: (m[i.status] ?? 0) + 1 }), {})
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="bg-card flex h-11 shrink-0 items-center gap-3 border-b px-4">
        <h2 className="text-sm font-semibold">Results</h2>
        <span className="tabular text-muted-foreground text-xs">
          {items.length} {items.length === 1 ? "field" : "fields"}
          {counts.supported ? ` · ${counts.supported} supported` : ""}
          {items.length - (counts.supported ?? 0) ? ` · ${items.length - (counts.supported ?? 0)} to review` : ""}
        </span>
        {review && (
          <a href={review.reportUrl} target="_blank" rel="noreferrer" className="text-primary ml-auto text-xs underline-offset-2 hover:underline">
            Open the report
          </a>
        )}
        {trailing}
      </div>
      {sampleLabel && (
        <p className="bg-evidence-soft text-evidence-foreground border-b px-4 py-1.5 text-xs">{sampleLabel}</p>
      )}
      <ul className="min-h-0 flex-1 space-y-2 overflow-y-auto p-3" aria-label="Extraction results">
        {items.length === 0 && (
          <li className="text-muted-foreground p-6 text-center text-sm">
            {running ? "The engine is reading the document. Progress is in Engine logs below." : "No results yet. Choose “New extraction” to run the engine on a document, or open the sample."}
          </li>
        )}
        {items.map((item) => {
          const selected = item.id === selectedId
          return (
            <li key={item.id}>
              <button
                type="button"
                aria-pressed={selected}
                onClick={() => {
                  onSelect(item.id)
                  onFocusHighlights(item.page, item.highlights)
                }}
                className={cn(
                  "bg-card w-full rounded-lg border p-3 text-left transition-[box-shadow,border-color,background-color] duration-150",
                  "hover:border-input focus-visible:ring-ring focus-visible:ring-2 focus-visible:outline-none",
                  selected && "border-primary ring-primary/25 shadow-sm ring-4"
                )}
              >
                <span className="flex items-start justify-between gap-3">
                  <span className="min-w-0">
                    <span className="block text-sm font-medium">{item.label}</span>
                    <span className="text-muted-foreground block text-xs">{KIND[item.kind]}</span>
                  </span>
                  <StatusBadge status={item.status} />
                </span>
                <span
                  className={cn(
                    "mt-2 block text-[15px] leading-snug break-words",
                    item.display ? "font-semibold" : "text-muted-foreground italic"
                  )}
                >
                  {item.display ?? (item.status === "not_found" ? "No value in the document" : "No accepted value")}
                </span>
              </button>
              {selected && <Detail item={item} onFocusHighlights={onFocusHighlights} review={review} />}
            </li>
          )
        })}
      </ul>
    </div>
  )
}

function Detail({ item, onFocusHighlights, review }: { item: ReviewItem; onFocusHighlights: Props["onFocusHighlights"]; review?: Props["review"] }) {
  const material = item.findings.filter((f) => f.severity === "material")
  const notes = item.findings.filter((f) => f.severity !== "material")
  return (
    <div className="bg-card/60 mx-1 mt-1 space-y-4 rounded-b-lg border border-t-0 p-3 text-sm">
      {item.records.length > 0 && (
        <section aria-label="Items">
          <h3 className="text-muted-foreground mb-1.5 text-xs font-semibold tracking-wide uppercase">Items found</h3>
          <ol className="space-y-1.5">
            {item.records.map((r, i) => (
              <li key={i}>
                <button
                  type="button"
                  onClick={() => onFocusHighlights(r.highlights[0]?.page ?? null, r.highlights, true)}
                  className="hover:bg-evidence-soft focus-visible:ring-ring w-full rounded-md border p-2 text-left focus-visible:ring-2 focus-visible:outline-none"
                >
                  <span className="flex gap-2">
                    {r.key && <span className="tabular text-muted-foreground shrink-0">{r.key}</span>}
                    <span>{r.text}</span>
                  </span>
                  {r.fields.length > 0 && (
                    <span className="text-muted-foreground mt-1 block text-xs">
                      {r.fields.map((f) => `${f.name}: ${f.value || "—"}`).join(" · ")}
                    </span>
                  )}
                </button>
              </li>
            ))}
          </ol>
        </section>
      )}

      {item.justification && (
        <section>
          <h3 className="text-muted-foreground mb-1 text-xs font-semibold tracking-wide uppercase">Why this label</h3>
          <p>{item.justification}</p>
        </section>
      )}

      {item.conditions.length > 0 && (
        <section>
          <h3 className="text-muted-foreground mb-1 text-xs font-semibold tracking-wide uppercase">Conditions</h3>
          <ul className="list-disc space-y-1 pl-5">
            {item.conditions.map((c, i) => (
              <li key={i}>{c}</li>
            ))}
          </ul>
        </section>
      )}

      <section>
        <h3 className="text-muted-foreground mb-1.5 flex items-center gap-1.5 text-xs font-semibold tracking-wide uppercase">
          <FileSearch className="size-3.5" aria-hidden /> Source
        </h3>
        {item.quotes.length === 0 && item.records.length === 0 && (
          <p className="text-muted-foreground">
            {item.status === "supported" ? "The engine did not return a quote for this answer." : "No source passage supports an answer."}
          </p>
        )}
        <div className="space-y-2">
          {item.quotes.map((q, i) => {
            const located = q.highlights.length > 0
            const Tag = located ? "button" : "div"
            return (
              <Tag
                key={i}
                {...(located
                  ? { type: "button" as const, onClick: () => onFocusHighlights(q.highlights[0].page, q.highlights, true) }
                  : {})}
                className={cn(
                  "block w-full rounded-md bg-evidence-soft px-3 py-2 text-left",
                  located && "hover:bg-evidence/30 focus-visible:ring-ring cursor-pointer focus-visible:ring-2 focus-visible:outline-none"
                )}
              >
                <span className="block break-words">“{q.text}”</span>
                <span className="text-evidence-foreground mt-1 block text-xs">
                  {q.label ? `${q.label} · ` : ""}
                  {located ? `Page ${q.highlights[0].page} · click to show on the page` : "Page not confirmed · quote only"}
                </span>
              </Tag>
            )
          })}
        </div>
        {item.scopeNote && <p className="text-muted-foreground mt-2 text-xs">{item.scopeNote}</p>}
      </section>

      <section aria-label="Validation">
        <h3 className="text-muted-foreground mb-1.5 text-xs font-semibold tracking-wide uppercase">Validation</h3>
        {item.reasons.length === 0 && material.length === 0 && notes.length === 0 && (
          <p className="text-muted-foreground">No validation notes.</p>
        )}
        {item.reasons.length > 0 && (
          <ul className="space-y-1">
            {item.reasons.map((r, i) => (
              <li key={i} className="flex gap-2">
                {r.startsWith("accepted") ? (
                  <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-ok" aria-hidden />
                ) : (
                  <AlertTriangle className="text-evidence-foreground mt-0.5 size-4 shrink-0" aria-hidden />
                )}
                <span>{r}</span>
              </li>
            ))}
          </ul>
        )}
        {[...material, ...notes].map((f, i) => (
          <p key={i} className={cn("mt-1.5 text-xs", f.severity === "material" ? "text-foreground" : "text-muted-foreground")}>
            <span className="font-medium">{f.category.replace(/_/g, " ")}</span>: {f.message}
          </p>
        ))}
      </section>

      {review && (
        <ReviewBar
          taskId={item.id}
          status={item.status}
          reviewed={review.items[item.id]}
          onReview={(decision, value, note) => review.onReview(item.id, decision, value, note)}
        />
      )}
    </div>
  )
}
