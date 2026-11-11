import { useState } from "react"
import { ChevronRight, Copy } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { cn } from "@workspace/ui/lib/utils"

function Node({ name, value, depth }: { name?: string; value: unknown; depth: number }) {
  const isObj = value !== null && typeof value === "object"
  const [open, setOpen] = useState(depth < 1)
  const key = name !== undefined ? <span className="text-primary">{name}: </span> : null
  if (!isObj) {
    const cls = typeof value === "string" ? "text-ok" : typeof value === "number" ? "text-warn" : "text-muted-foreground"
    return (
      <div className="pl-4 break-words">
        {key}
        <span className={cls}>{typeof value === "string" ? JSON.stringify(value) : String(value)}</span>
      </div>
    )
  }
  const entries = Array.isArray(value) ? value.map((v, i) => [String(i), v] as const) : Object.entries(value as object)
  const brace = Array.isArray(value) ? ["[", "]"] : ["{", "}"]
  return (
    <div>
      <button type="button" onClick={() => setOpen(!open)} className="hover:bg-secondary flex items-center rounded text-left">
        <ChevronRight className={cn("size-3.5 shrink-0 transition-transform", open && "rotate-90")} aria-hidden />
        {key}
        <span className="text-muted-foreground">
          {brace[0]}
          {!open && ` ${entries.length} ${Array.isArray(value) ? "items" : "keys"} `}
          {!open && brace[1]}
        </span>
      </button>
      {open && (
        <div className="border-border ml-1.5 border-l pl-2">
          {entries.map(([k, v]) => (
            <Node key={k} name={k} value={v} depth={depth + 1} />
          ))}
        </div>
      )}
    </div>
  )
}

/** Collapsible JSON tree for any engine payload. */
export function JsonView({ value, className }: { value: unknown; className?: string }) {
  return (
    <div className={cn("bg-card rounded-md border", className)}>
      <div className="flex justify-end border-b px-2 py-1">
        <Button variant="ghost" size="xs" onClick={() => void navigator.clipboard?.writeText(JSON.stringify(value, null, 2))}>
          <Copy aria-hidden /> Copy
        </Button>
      </div>
      <div className="max-h-[60vh] overflow-auto p-2 font-mono text-xs leading-relaxed">
        <Node value={value} depth={0} />
      </div>
    </div>
  )
}

type Cell = number | string | boolean

/** Flattens the scalar values of a result so the headline metrics can be scanned as a table. */
export function metricRows(value: unknown, prefix = "", out: [string, Cell][] = [], depth = 0): [string, Cell][] {
  if (out.length > 60 || depth > 3 || value === null || value === undefined) return out
  if (typeof value === "number" || typeof value === "boolean") out.push([prefix, value])
  else if (typeof value === "string") {
    if (value.length <= 60 && prefix) out.push([prefix, value])
  } else if (Array.isArray(value)) {
    if (value.length <= 8 && value.every((v) => typeof v !== "object")) out.push([prefix, value.join(", ")])
  } else {
    for (const [k, v] of Object.entries(value as object)) metricRows(v, prefix ? `${prefix}.${k}` : k, out, depth + 1)
  }
  return out
}

export function MetricsTable({ value }: { value: unknown }) {
  const rows = metricRows(value)
  if (!rows.length) return null
  return (
    <div className="bg-card max-h-72 overflow-auto rounded-md border">
      <table className="w-full text-xs">
        <tbody>
          {rows.map(([k, v]) => (
            <tr key={k} className="border-b last:border-0">
              <td className="text-muted-foreground px-2 py-1 font-mono">{k}</td>
              <td className="tabular px-2 py-1 text-right font-medium">{typeof v === "number" && !Number.isInteger(v) ? v.toFixed(3) : String(v)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
