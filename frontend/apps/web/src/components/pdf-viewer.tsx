import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import * as pdfjs from "pdfjs-dist"
import type { PDFDocumentProxy } from "pdfjs-dist"
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url"
import { ChevronLeft, ChevronRight, Minus, Plus, ScanLine } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import type { Highlight } from "@/lib/types"

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl

const MIN_ZOOM = 0.5
const MAX_ZOOM = 3
const GUTTER = 40

interface PdfViewerProps {
  url: string
  /** amber boxes to draw (only blocks with reliable coordinates) */
  highlights: Highlight[]
  /** change `token` to scroll to `page` (and to the first highlight on it) */
  jump: { page: number; token: number } | null
}

export function PdfViewer({ url, highlights, jump }: PdfViewerProps) {
  const [doc, setDoc] = useState<PDFDocumentProxy | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [pageSizes, setPageSizes] = useState<{ w: number; h: number }[]>([])
  const [zoom, setZoom] = useState(1)
  const [width, setWidth] = useState(0)
  const [current, setCurrent] = useState(1)
  const scroller = useRef<HTMLDivElement>(null)
  const pageEls = useRef<(HTMLDivElement | null)[]>([])

  useEffect(() => {
    let cancelled = false
    const task = pdfjs.getDocument({ url })
    task.promise
      .then(async (d) => {
        const sizes: { w: number; h: number }[] = []
        for (let i = 1; i <= d.numPages; i++) {
          const v = (await d.getPage(i)).getViewport({ scale: 1 })
          sizes.push({ w: v.width, h: v.height })
        }
        if (cancelled) return
        setPageSizes(sizes)
        setDoc(d)
      })
      .catch((e: Error) => !cancelled && setError(e.message))
    return () => {
      cancelled = true
      void task.destroy()
    }
  }, [url])

  useEffect(() => {
    const el = scroller.current
    if (!el) return
    const ro = new ResizeObserver(() => setWidth(el.clientWidth))
    ro.observe(el)
    setWidth(el.clientWidth)
    return () => ro.disconnect()
  }, [])

  // CSS pixels per PDF point: pages fit the width at zoom 1
  const scale = useMemo(() => {
    const first = pageSizes[0]?.w ?? 595
    return Math.max(0.2, ((width - GUTTER) / first) * zoom)
  }, [width, zoom, pageSizes])

  const goTo = useCallback(
    (page: number, y?: number) => {
      const el = pageEls.current[page - 1]
      const sc = scroller.current
      if (!el || !sc) return
      const offset = y !== undefined ? Math.max(0, y * scale - 96) : 0
      sc.scrollTo({ top: el.offsetTop - 16 + offset, behavior: "smooth" })
    },
    [scale]
  )

  useEffect(() => {
    if (!jump || !doc) return
    const first = highlights.filter((h) => h.page === jump.page).sort((a, b) => a.bbox[1] - b.bbox[1])[0]
    goTo(jump.page, first?.bbox[1])
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jump?.token, doc])

  const onScroll = () => {
    const sc = scroller.current
    if (!sc) return
    const mid = sc.scrollTop + sc.clientHeight * 0.3
    let page = 1
    pageEls.current.forEach((el, i) => {
      if (el && el.offsetTop <= mid) page = i + 1
    })
    setCurrent(page)
  }

  const total = pageSizes.length
  const zoomBy = (f: number) => setZoom((z) => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, +(z * f).toFixed(3))))

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="bg-card flex h-11 shrink-0 items-center gap-1 border-b px-3">
        <Button variant="ghost" size="icon-sm" aria-label="Previous page" disabled={current <= 1} onClick={() => goTo(current - 1)}>
          <ChevronLeft />
        </Button>
        <span className="tabular min-w-16 text-center text-sm" aria-live="polite">
          {total ? `${current} / ${total}` : "–"}
        </span>
        <Button variant="ghost" size="icon-sm" aria-label="Next page" disabled={current >= total} onClick={() => goTo(current + 1)}>
          <ChevronRight />
        </Button>
        <div className="flex-1" />
        <Button variant="ghost" size="icon-sm" aria-label="Zoom out" disabled={zoom <= MIN_ZOOM} onClick={() => zoomBy(1 / 1.2)}>
          <Minus />
        </Button>
        <span className="tabular w-12 text-center text-sm">{Math.round(zoom * 100)}%</span>
        <Button variant="ghost" size="icon-sm" aria-label="Zoom in" disabled={zoom >= MAX_ZOOM} onClick={() => zoomBy(1.2)}>
          <Plus />
        </Button>
        <Button variant="ghost" size="icon-sm" aria-label="Fit to width" onClick={() => setZoom(1)}>
          <ScanLine />
        </Button>
      </div>
      <div ref={scroller} onScroll={onScroll} className="bg-paper min-h-0 flex-1 overflow-auto" tabIndex={0} aria-label="Document pages">
        {error && <p className="text-destructive p-6 text-sm">Could not open the document: {error}</p>}
        {!doc && !error && <p className="text-muted-foreground p-6 text-sm">Opening document…</p>}
        {doc && (
          <div className="flex w-max min-w-full flex-col items-center gap-4 p-5">
            {pageSizes.map((s, i) => (
              <PageView
                key={i}
                doc={doc}
                pageNumber={i + 1}
                scale={scale}
                size={s}
                scroller={scroller}
                highlights={highlights.filter((h) => h.page === i + 1)}
                refEl={(el) => {
                  pageEls.current[i] = el
                }}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

interface PageViewProps {
  doc: PDFDocumentProxy
  pageNumber: number
  scale: number
  size: { w: number; h: number }
  scroller: React.RefObject<HTMLDivElement | null>
  highlights: Highlight[]
  refEl: (el: HTMLDivElement | null) => void
}

function PageView({ doc, pageNumber, scale, size, scroller, highlights, refEl }: PageViewProps) {
  const box = useRef<HTMLDivElement | null>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const [visible, setVisible] = useState(pageNumber <= 2)
  const cssW = size.w * scale
  const cssH = size.h * scale

  useEffect(() => {
    const el = box.current
    if (!el) return
    const io = new IntersectionObserver(([e]) => e && setVisible(e.isIntersecting), {
      root: scroller.current,
      rootMargin: "800px 0px",
    })
    io.observe(el)
    return () => io.disconnect()
  }, [scroller])

  useEffect(() => {
    if (!visible || !canvas.current) return
    let cancelled = false
    let task: { cancel: () => void; promise: Promise<unknown> } | null = null
    void doc.getPage(pageNumber).then((page) => {
      if (cancelled || !canvas.current) return
      const dpr = window.devicePixelRatio || 1
      const viewport = page.getViewport({ scale: scale * dpr })
      const c = canvas.current
      c.width = Math.floor(viewport.width)
      c.height = Math.floor(viewport.height)
      task = page.render({ canvasContext: c.getContext("2d")!, viewport })
      task.promise.catch(() => undefined)
    })
    return () => {
      cancelled = true
      task?.cancel()
    }
  }, [doc, pageNumber, scale, visible])

  return (
    <div
      ref={(el) => {
        box.current = el
        refEl(el)
      }}
      className="relative bg-white shadow-[0_1px_2px_rgb(60_50_30/0.18),0_8px_24px_-8px_rgb(60_50_30/0.25)]"
      style={{ width: cssW, height: cssH }}
      data-page={pageNumber}
    >
      <canvas ref={canvas} style={{ width: cssW, height: cssH }} className="block" />
      {highlights.map((h) => (
        <div
          key={h.blockId}
          data-highlight={h.blockId}
          className="bg-evidence/45 outline-evidence pointer-events-none absolute rounded-[2px] outline-2 outline-offset-1 mix-blend-multiply"
          style={{
            left: h.bbox[0] * scale - 3,
            top: h.bbox[1] * scale - 2,
            width: (h.bbox[2] - h.bbox[0]) * scale + 6,
            height: (h.bbox[3] - h.bbox[1]) * scale + 4,
          }}
        />
      ))}
    </div>
  )
}
