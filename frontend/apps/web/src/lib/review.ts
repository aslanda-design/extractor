import type {
  BlockInfo,
  BlocksData,
  Highlight,
  Quote,
  ReviewItem,
  SourceRef,
  TaskResult,
} from "./types"

export function humanize(id: string): string {
  const words = id.replace(/[_-]+/g, " ").trim().split(" ")
  const ACRONYMS = new Set(["sow", "id", "url", "vat", "iban", "ssn", "nda", "sla"])
  const out = words.map((w, i) => (ACRONYMS.has(w.toLowerCase()) ? w.toUpperCase() : i === 0 ? w.charAt(0).toUpperCase() + w.slice(1) : w))
  return out.join(" ")
}

export function formatValue(v: unknown): string {
  if (v === null || v === undefined) return ""
  if (typeof v === "string") return v
  if (typeof v === "number" || typeof v === "boolean") return String(v)
  if (Array.isArray(v)) return v.map(formatValue).join(", ")
  return JSON.stringify(v)
}

function highlightsFor(refs: SourceRef[], blocks: Map<string, BlockInfo>): Highlight[] {
  const out: Highlight[] = []
  const seen = new Set<string>()
  for (const r of refs) {
    const b = blocks.get(r.block_id)
    if (!b || seen.has(b.id)) continue
    seen.add(b.id)
    out.push({ blockId: b.id, page: b.page, bbox: b.bbox })
  }
  return out
}

function uniq(hs: Highlight[]): Highlight[] {
  const m = new Map<string, Highlight>()
  hs.forEach((h) => m.set(h.blockId, h))
  return [...m.values()].sort((a, b) => a.page - b.page || a.bbox[1] - b.bbox[1])
}

/** Turns an engine result into what the review screen needs. Coordinates are only used when the block is known. */
export function toReviewItems(results: TaskResult[], data: BlocksData | null): ReviewItem[] {
  const blocks = new Map((data?.blocks ?? []).map((b) => [b.id, b]))
  return results.map((r) => {
    const quotes: Quote[] = []
    if (r.interpretation?.stances.length) {
      for (const s of r.interpretation.stances) {
        if (s.quote) quotes.push({ text: s.quote, role: s.role, label: s.label, highlights: highlightsFor(s.evidence, blocks) })
      }
    }
    if (!quotes.length) {
      for (const e of r.evidence) {
        if (e.quote) quotes.push({ text: e.quote, role: e.role, highlights: highlightsFor([e.ref], blocks) })
      }
    }
    // no quote text but cited blocks: show the block text itself as the quote
    if (!quotes.length) {
      for (const e of r.evidence) {
        const b = blocks.get(e.ref.block_id)
        if (b) quotes.push({ text: b.text, role: e.role, highlights: highlightsFor([e.ref], blocks) })
      }
    }
    const records = r.records.map((rec) => ({
      key: rec.key,
      text: rec.text,
      fields: rec.fields.map((f) => ({ name: humanize(f.field_id), value: formatValue(f.value) })),
      highlights: highlightsFor(
        [...rec.block_ids.map((block_id) => ({ document_id: "", block_id })), ...rec.fields.flatMap((f) => f.evidence)],
        blocks
      ),
    }))
    const refs = [
      ...r.evidence.map((e) => e.ref),
      ...(r.interpretation?.stances.flatMap((s) => s.evidence) ?? []),
    ]
    const highlights = uniq([
      ...highlightsFor(refs, blocks),
      ...records.flatMap((x) => x.highlights),
    ])
    let display: string | null = null
    if (r.kind === "records") display = `${r.records.length} ${r.records.length === 1 ? "item" : "items"}`
    else if (r.kind === "interpretation") display = r.status === "supported" ? (r.interpretation?.label ?? null) : null
    else if (r.status === "supported") display = formatValue(r.value) || null
    return {
      id: r.task_id,
      label: humanize(r.task_id),
      kind: r.kind,
      status: r.status,
      display,
      quotes,
      findings: r.validation.findings,
      reasons: r.acceptance?.reasons ?? [],
      conditions: r.interpretation?.conditions ?? [],
      justification: r.interpretation?.justification ?? "",
      scopeNote: r.scope_note,
      records,
      highlights,
      page: highlights.length ? highlights[0].page : null,
    }
  })
}
