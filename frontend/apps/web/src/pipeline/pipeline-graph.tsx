import { useEffect, useMemo, useRef } from "react"
import { CheckCircle2, CircleHelp, CircleSlash, Crosshair, RotateCcw, Scale, ScanText, Search, ShieldCheck, XCircle, type LucideIcon } from "lucide-react"

import { cn } from "@workspace/ui/lib/utils"
import type { Status } from "@/lib/types"
import { LAYERS, type FieldState, type NodeId, type PipelineState } from "./model"

// --- geometry ---------------------------------------------------------------------------------

interface Pt {
  x: number
  y: number
}
const W = 1040
const H = 540
const R = 27

type Place = { x: number; y: number; label: string; code: string; Icon: LucideIcon }
const NODES: Record<Exclude<NodeId, "result">, Place> = {
  scope: { x: 78, y: 250, label: "Scope", code: "S01", Icon: Crosshair },
  retrieve: { x: 238, y: 250, label: "Retrieve", code: "S06", Icon: Search },
  read: { x: 410, y: 250, label: "Read", code: "S07-S09", Icon: ScanText },
  validate: { x: 594, y: 250, label: "Validate", code: "S10", Icon: ShieldCheck },
  decide: { x: 770, y: 250, label: "Decide", code: "S11", Icon: Scale },
  recover: { x: 594, y: 458, label: "Recover", code: "S12", Icon: RotateCcw },
}

const BINS: Record<Status, Pt & { label: string; Icon: LucideIcon; tone: string }> = {
  supported: { x: 950, y: 112, label: "Supported", Icon: CheckCircle2, tone: "var(--ok)" },
  not_found: { x: 950, y: 232, label: "Not found", Icon: CircleSlash, tone: "var(--muted-foreground)" },
  uncertain: { x: 950, y: 352, label: "Uncertain", Icon: CircleHelp, tone: "var(--warn)" },
  failed: { x: 950, y: 472, label: "Failed", Icon: XCircle, tone: "var(--bad)" },
}
const BIN_W = 112
const BIN_H = 84

const KIND_COLOR = { value: "var(--kind-value)", interpretation: "var(--kind-interpretation)", records: "var(--kind-records)" }
const STATUS_COLOR: Record<Status, string> = { supported: "var(--ok)", not_found: "var(--muted-foreground)", uncertain: "var(--warn)", failed: "var(--bad)" }

type Curve = "flow" | "down" | "up" | "arc"
interface EdgeDef {
  key: string
  from: Pt
  to: Pt
  curve: Curve
  tone: string
}

const E = (a: keyof typeof NODES, b: keyof typeof NODES, curve: Curve, tone = "var(--primary)"): EdgeDef => {
  const A = NODES[a]
  const B = NODES[b]
  const horizontal = curve === "flow"
  return {
    key: `${a}>${b}`,
    curve,
    tone,
    from: horizontal ? { x: A.x + R + 3, y: A.y } : curve === "down" ? { x: A.x, y: A.y + R + 3 } : { x: A.x - R - 3, y: A.y },
    to: horizontal ? { x: B.x - R - 3, y: B.y } : curve === "down" ? { x: B.x + R + 3, y: B.y } : { x: B.x, y: B.y + R + 3 },
  }
}
const binEdge = (s: Status): EdgeDef => ({
  key: `decide>bin:${s}`,
  curve: "flow",
  tone: STATUS_COLOR[s],
  from: { x: NODES.decide.x + R + 3, y: NODES.decide.y },
  to: { x: BINS[s].x - BIN_W / 2 - 4, y: BINS[s].y },
})
const EDGES: EdgeDef[] = [
  E("scope", "retrieve", "flow"),
  E("retrieve", "read", "flow"),
  E("read", "validate", "flow"),
  E("validate", "decide", "flow"),
  E("decide", "recover", "down", "var(--evidence)"),
  E("recover", "read", "up", "var(--evidence)"),
  ...(["supported", "not_found", "uncertain", "failed"] as Status[]).map(binEdge),
]

function controls(c: Curve, p0: Pt, p3: Pt): [Pt, Pt] {
  const dx = p3.x - p0.x
  const dy = p3.y - p0.y
  if (c === "flow") return [{ x: p0.x + dx * 0.45, y: p0.y }, { x: p3.x - dx * 0.45, y: p3.y }]
  if (c === "down") return [{ x: p0.x, y: p0.y + dy * 0.85 }, { x: p3.x + 90, y: p3.y }]
  if (c === "up") return [{ x: p0.x - 120, y: p0.y }, { x: p3.x, y: p3.y + 130 }]
  const len = Math.hypot(dx, dy) || 1
  const nx = (-dy / len) * len * 0.22
  const ny = (dx / len) * len * 0.22
  return [{ x: p0.x + dx * 0.3 + nx, y: p0.y + dy * 0.3 + ny }, { x: p0.x + dx * 0.7 + nx, y: p0.y + dy * 0.7 + ny }]
}

const pathD = (e: EdgeDef) => {
  const [c1, c2] = controls(e.curve, e.from, e.to)
  return `M${e.from.x},${e.from.y} C${c1.x},${c1.y} ${c2.x},${c2.y} ${e.to.x},${e.to.y}`
}

function bezier(p0: Pt, c1: Pt, c2: Pt, p3: Pt, t: number): Pt {
  const u = 1 - t
  return {
    x: u * u * u * p0.x + 3 * u * u * t * c1.x + 3 * u * t * t * c2.x + t * t * t * p3.x,
    y: u * u * u * p0.y + 3 * u * u * t * c1.y + 3 * u * t * t * c2.y + t * t * t * p3.y,
  }
}

const destNode = (f: FieldState) => (f.node === "result" ? (f.outcome ?? "failed") : f.node)
const edgeKey = (f: FieldState) => {
  const from = f.from ?? "scope"
  return f.node === "result" ? `decide>bin:${f.outcome ?? "failed"}` : `${from}>${f.node}`
}
const curveFor = (f: FieldState): Curve => EDGES.find((e) => e.key === edgeKey(f))?.curve ?? "arc"

/** Where a field rests: on a ring around its node, or in a grid inside its result bin. */
function slot(f: FieldState, idx: number, n: number, t: number, still: boolean): Pt {
  if (f.node === "result") {
    const b = BINS[f.outcome ?? "failed"]
    const cols = 6
    return { x: b.x - BIN_W / 2 + 17 + (idx % cols) * 16.5, y: b.y + 6 + Math.floor(idx / cols) * 16 }
  }
  const n0 = NODES[f.node]
  const ring = idx < 8 ? 0 : 1
  const m = ring ? n - 8 : Math.min(n, 8)
  const spin = still ? 0 : t * (ring ? -0.00028 : 0.00042)
  const a = ((idx - ring * 8) / Math.max(m, 1)) * Math.PI * 2 + spin + ring * 0.5 - Math.PI / 2
  const r = ring ? R + 42 : R + 26
  return { x: n0.x + Math.cos(a) * r, y: n0.y + Math.sin(a) * r }
}

interface Particle {
  x: number
  y: number
  moves: number
  route: { c: Curve; p0: Pt; p3: Pt; t0: number; dur: number } | null
  trail: Pt[]
}

// --- component --------------------------------------------------------------------------------

interface Props {
  state: PipelineState
  selectedId: string | null
  hoverId: string | null
  onSelect: (id: string | null) => void
}

export function PipelineGraph({ state, selectedId, hoverId, onSelect }: Props) {
  const fields = useMemo(() => state.order.map((id) => state.fields[id]), [state.order, state.fields])
  const fieldsRef = useRef(fields)
  const focusRef = useRef({ selectedId, hoverId })
  const parts = useRef(new Map<string, Particle>())
  const els = useRef(new Map<string, { g: SVGGElement | null; trail: SVGPathElement | null; core: SVGCircleElement | null; label: SVGTextElement | null }>())
  const edgeEls = useRef(new Map<string, SVGPathElement | null>())
  const heat = useRef(new Map<string, number>())
  /** Child refs attach before their parent's, so the entry is created by whichever comes first. */
  const entry = (id: string) => {
    let e = els.current.get(id)
    if (!e) {
      e = { g: null, trail: null, core: null, label: null }
      els.current.set(id, e)
    }
    return e
  }

  useEffect(() => {
    fieldsRef.current = fields
    focusRef.current = { selectedId, hoverId }
  })

  useEffect(() => {
    const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches
    let raf = 0
    let last = 0
    const frame = (now: number) => {
      const dt = last ? Math.min(now - last, 64) : 16
      last = now
      const list = fieldsRef.current
      const counts = new Map<string, number>()
      for (const f of list) {
        const key = destNode(f)
        const idx = counts.get(key) ?? 0
        counts.set(key, idx + 1)
        const n = list.filter((x) => destNode(x) === key).length
        let p = parts.current.get(f.id)
        const target = slot(f, idx, n, now, reduce)
        if (!p) {
          p = { x: NODES.scope.x - 62, y: NODES.scope.y + (idx % 2 ? 10 : -10), moves: f.moves, route: null, trail: [] }
          parts.current.set(f.id, p)
        }
        if (p.moves !== f.moves) {
          p.moves = f.moves
          const dist = Math.hypot(target.x - p.x, target.y - p.y)
          p.route = reduce ? null : { c: curveFor(f), p0: { x: p.x, y: p.y }, p3: target, t0: now, dur: Math.min(1250, 520 + dist * 1.15) }
          if (reduce) Object.assign(p, target)
          heat.current.set(edgeKey(f), 1)
        }
        if (p.route) {
          const k = Math.min(1, (now - p.route.t0) / p.route.dur)
          const eased = 1 - Math.pow(1 - k, 3)
          const [c1, c2] = controls(p.route.c, p.route.p0, p.route.p3)
          const pt = bezier(p.route.p0, c1, c2, { x: target.x, y: target.y }, eased)
          p.x = pt.x
          p.y = pt.y
          if (k >= 1) p.route = null
        } else {
          const a = 1 - Math.exp(-dt / 110)
          p.x += (target.x - p.x) * a
          p.y += (target.y - p.y) * a
        }
        const moving = p.route !== null
        p.trail.push({ x: p.x, y: p.y })
        if (p.trail.length > (moving ? 26 : 1)) p.trail.splice(0, p.trail.length - (moving ? 26 : 1))
        const el = els.current.get(f.id)
        if (el?.g) {
          el.g.setAttribute("transform", `translate(${p.x.toFixed(1)} ${p.y.toFixed(1)})`)
          el.trail?.setAttribute("d", moving && p.trail.length > 1 ? "M" + p.trail.map((q) => `${(q.x - p.x).toFixed(1)},${(q.y - p.y).toFixed(1)}`).join(" L") : "")
          const sel = focusRef.current.selectedId === f.id
          const hov = focusRef.current.hoverId === f.id
          el.core?.setAttribute("r", String((sel ? 6.8 : hov ? 6.2 : 5) + (reduce || f.node === "result" ? 0 : Math.sin(now / 380 + idx) * 0.5)))
          el.label?.setAttribute("opacity", sel || hov ? "1" : "0")
        }
      }
      for (const [key, node] of edgeEls.current) {
        const h = Math.max(0, (heat.current.get(key) ?? 0) - dt / 2600)
        heat.current.set(key, h)
        node?.setAttribute("opacity", String(h > 0.02 ? 0.12 + h * 0.88 : 0))
      }
      raf = requestAnimationFrame(frame)
    }
    raf = requestAnimationFrame(frame)
    return () => cancelAnimationFrame(raf)
  }, [])

  const occupancy = useMemo(() => {
    const m: Record<string, FieldState[]> = {}
    for (const f of fields) (m[f.node === "result" ? `bin:${f.outcome}` : f.node] ??= []).push(f)
    return m
  }, [fields])

  const focus = fields.find((f) => f.id === (selectedId ?? hoverId)) ?? [...fields].sort((a, b) => (b.visits.at(-1)?.at ?? 0) - (a.visits.at(-1)?.at ?? 0))[0]
  const layers = focus?.layers ?? state.lastLayers
  const loops = fields.reduce((n, f) => n + f.loops, 0)

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="block h-full w-full" role="img" aria-label="Live pipeline graph: every field moving through the engine's stages">
      <defs>
        <pattern id="pg-dots" width="26" height="26" patternUnits="userSpaceOnUse">
          <circle cx="1.5" cy="1.5" r="1" fill="var(--foreground)" opacity="0.1" />
        </pattern>
        <filter id="pg-glow" x="-200%" y="-200%" width="500%" height="500%">
          <feGaussianBlur stdDeviation="3.2" result="b" />
          <feMerge>
            <feMergeNode in="b" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
        <filter id="pg-lift" x="-40%" y="-40%" width="180%" height="190%">
          <feDropShadow dx="0" dy="5" stdDeviation="6" floodColor="#000" floodOpacity="0.45" />
        </filter>
        <linearGradient id="pg-node" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="var(--card)" />
          <stop offset="1" stopColor="var(--background)" />
        </linearGradient>
      </defs>
      <rect width={W} height={H} fill="url(#pg-dots)" onClick={() => onSelect(null)} />

      {/* edges: a quiet rail, and a flowing overlay that lights where fields just travelled */}
      {EDGES.map((e) => (
        <g key={e.key} fill="none">
          <path d={pathD(e)} stroke="var(--border)" strokeWidth="1.5" strokeDasharray={e.curve === "flow" ? undefined : "3 6"} strokeLinecap="round" />
          <path
            ref={(n) => void edgeEls.current.set(e.key, n)}
            d={pathD(e)}
            stroke={e.tone}
            strokeWidth="2.4"
            strokeDasharray="5 9"
            strokeLinecap="round"
            opacity="0"
            className="pipe-flow"
          />
        </g>
      ))}

      {/* result bins */}
      {(Object.keys(BINS) as Status[]).map((s) => {
        const b = BINS[s]
        const here = occupancy[`bin:${s}`]?.length ?? 0
        return (
          <g key={s} transform={`translate(${b.x} ${b.y})`}>
            <rect x={-BIN_W / 2} y={-BIN_H / 2} width={BIN_W} height={BIN_H} rx="14" fill="url(#pg-node)" stroke={here ? b.tone : "var(--border)"} strokeWidth="1.5" filter="url(#pg-lift)" style={{ transition: "stroke 400ms" }} />
            <b.Icon x={-BIN_W / 2 + 11} y={-BIN_H / 2 + 10} size={15} stroke={b.tone} strokeWidth={2} aria-hidden />
            <text x={-BIN_W / 2 + 32} y={-BIN_H / 2 + 22} fontSize="11.5" fontWeight="600" fill="var(--foreground)">
              {b.label}
            </text>
            <text x={BIN_W / 2 - 10} y={-BIN_H / 2 + 22} fontSize="12" fontWeight="600" textAnchor="end" fill={here ? b.tone : "var(--muted-foreground)"} className="tabular">
              {here}
            </text>
          </g>
        )
      })}

      {/* stage nodes */}
      {(Object.keys(NODES) as (keyof typeof NODES)[]).map((id) => {
        const n = NODES[id]
        const here = occupancy[id]?.length ?? 0
        const live = here > 0
        const isRecover = id === "recover"
        const tone = isRecover ? "var(--evidence)" : "var(--primary)"
        return (
          <g key={id} transform={`translate(${n.x} ${n.y})`}>
            {live && <circle r={R + 6} fill="none" stroke={tone} strokeWidth="1.5" className="pipe-ping" />}
            <circle r={R} fill="url(#pg-node)" stroke={live ? tone : "var(--border)"} strokeWidth={live ? 2 : 1.5} filter="url(#pg-lift)" style={{ transition: "stroke 300ms" }} />
            {live && <circle r={R + 6} fill="none" stroke={tone} strokeWidth="2" strokeDasharray="26 120" strokeLinecap="round" className="pipe-spin" />}
            <n.Icon x={-10} y={-10} size={20} stroke={live ? tone : "var(--muted-foreground)"} strokeWidth={1.8} aria-hidden style={{ transition: "stroke 300ms" }} />
            {id === "validate" && <LayerRing layers={layers} />}
            <text y={R + 30} textAnchor="middle" fontSize="12.5" fontWeight="600" fill="var(--foreground)">
              {n.label}
            </text>
            <text y={R + 44} textAnchor="middle" fontSize="10" fill="var(--muted-foreground)" className="tabular">
              {n.code}
            </text>
            {here > 0 && (
              <g transform={`translate(${R - 2} ${-R + 2})`}>
                <circle r="9" fill={tone} />
                <text y="3.6" textAnchor="middle" fontSize="10.5" fontWeight="700" fill="var(--primary-foreground)" className="tabular">
                  {here}
                </text>
              </g>
            )}
            {isRecover && loops > 0 && (
              <text y={R + 58} textAnchor="middle" fontSize="10.5" fill="var(--evidence)" className="tabular">
                {loops} recovery {loops === 1 ? "pass" : "passes"} so far
              </text>
            )}
          </g>
        )
      })}

      {/* fields */}
      {fields.map((f) => {
        const color = f.node === "result" ? STATUS_COLOR[f.outcome ?? "failed"] : KIND_COLOR[f.kind]
        const sel = selectedId === f.id
        return (
          <g key={f.id} ref={(g) => void (entry(f.id).g = g)} transform={`translate(${NODES.scope.x - 62} ${NODES.scope.y})`}>
            <path ref={(t) => void (entry(f.id).trail = t)} fill="none" stroke={color} strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" opacity="0.4" />
            {sel && <circle r="12" fill="none" stroke="var(--evidence)" strokeWidth="1.6" className="pipe-breathe" />}
            <circle r="5" fill={color} filter="url(#pg-glow)" ref={(c) => void (entry(f.id).core = c)} style={{ transition: "fill 400ms" }} />
            <circle r="13" fill="transparent" style={{ cursor: "pointer" }} onClick={() => onSelect(sel ? null : f.id)}>
              <title>{f.id}</title>
            </circle>
            <text
              ref={(t) => void (entry(f.id).label = t)}
              y="-15"
              textAnchor="middle"
              fontSize="11"
              fontWeight="600"
              fill="var(--foreground)"
              stroke="var(--background)"
              strokeWidth="3.5"
              paintOrder="stroke"
              opacity="0"
              pointerEvents="none"
            >
              {f.id}
            </text>
          </g>
        )
      })}

      {fields.length === 0 && (
        <text x={W / 2} y={H / 2 + 6} textAnchor="middle" fontSize="14" fill="var(--muted-foreground)">
          {state.running ? "Waiting for the first field..." : "Start an extraction, replay the last one, or run the simulated demo."}
        </text>
      )}
    </svg>
  )
}

/** Seven arcs around the Validate node: one per validation layer, lit by the field in focus. */
function LayerRing({ layers }: { layers: Record<string, number> }) {
  const r = R + 15
  const step = (Math.PI * 2) / LAYERS.length
  return (
    <g fill="none" strokeLinecap="round" strokeWidth="3.2">
      {LAYERS.map((name, i) => {
        const a0 = -Math.PI / 2 + i * step + 0.12
        const a1 = -Math.PI / 2 + (i + 1) * step - 0.12
        const v = layers[name]
        const color = v === undefined ? "var(--border)" : v ? "var(--warn)" : "var(--ok)"
        return (
          <path
            key={name}
            d={`M${Math.cos(a0) * r},${Math.sin(a0) * r} A${r},${r} 0 0 1 ${Math.cos(a1) * r},${Math.sin(a1) * r}`}
            stroke={color}
            style={{ transition: "stroke 350ms" }}
            className={cn(v !== undefined && "pipe-rise")}
          >
            <title>{`${name}${v === undefined ? ": not run" : v ? `: ${v} finding(s)` : ": ok"}`}</title>
          </path>
        )
      })}
    </g>
  )
}

export { KIND_COLOR, STATUS_COLOR }
export type { Props as PipelineGraphProps }
