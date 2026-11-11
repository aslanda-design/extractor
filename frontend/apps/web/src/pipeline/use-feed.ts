import { useCallback, useEffect, useReducer, useRef, useState } from "react"

import type { EngineEvent } from "@/lib/types"
import { initialState, reduce, type PipelineState } from "./model"

type Action = { type: "event"; e: EngineEvent } | { type: "load"; events: EngineEvent[] } | { type: "reset" }

function reducer(s: PipelineState, a: Action): PipelineState {
  if (a.type === "reset") return initialState()
  if (a.type === "load") return a.events.reduce(reduce, initialState())
  return reduce(s, a.e)
}

const wall = () => Date.now() / 1000

/** The pipeline model fed by live events, by a stored run (instantly) or by a replay (paced). */
export function usePipelineFeed() {
  const [state, dispatch] = useReducer(reducer, undefined, initialState)
  const [replaying, setReplaying] = useState(false)
  const timers = useRef<number[]>([])

  const stop = useCallback(() => {
    timers.current.forEach(clearTimeout)
    timers.current = []
    setReplaying(false)
  }, [])
  useEffect(() => stop, [stop])

  const reset = useCallback(() => {
    stop()
    dispatch({ type: "reset" })
  }, [stop])
  const push = useCallback((e: EngineEvent) => dispatch({ type: "event", e }), [])
  /** Shows the final picture of a stored run at once. */
  const load = useCallback(
    (events: EngineEvent[]) => {
      stop()
      dispatch({ type: "load", events })
    },
    [stop]
  )

  /** Plays events on a schedule that keeps the engine's rhythm but compresses long waits (a model call can take a minute). */
  const play = useCallback(
    (events: { at: number; e: Omit<EngineEvent, "seq" | "time" | "run"> }[], onDone?: () => void) => {
      stop()
      dispatch({ type: "reset" })
      setReplaying(true)
      let seq = 0
      events.forEach(({ at, e }, i) => {
        timers.current.push(
          window.setTimeout(() => {
            dispatch({ type: "event", e: { ...e, seq: ++seq, time: wall(), run: "replay" } })
            if (i === events.length - 1) {
              setReplaying(false)
              onDone?.()
            }
          }, at * 1000)
        )
      })
    },
    [stop]
  )

  const replay = useCallback(
    (events: EngineEvent[]) => {
      if (!events.length) return
      let at = 0
      const items = events.map((e, i) => {
        if (i > 0) at += Math.min(Math.max(e.time - events[i - 1].time, 0), 1.5)
        return { at, e }
      })
      const last = items[items.length - 1]
      items.push({ at: last.at + 0.8, e: { level: "ok", stage: "run", message: "Replay finished", kind: "run_end", data: {} } as never })
      play(items)
    },
    [play]
  )

  return { state, replaying, reset, push, load, play, replay, stop }
}
