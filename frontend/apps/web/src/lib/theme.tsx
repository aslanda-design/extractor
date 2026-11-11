import { useEffect, useState } from "react"
import { Monitor, Moon, Sun } from "lucide-react"

import { Button } from "@workspace/ui/components/button"

export type Theme = "light" | "dark" | "system"
const ORDER: Theme[] = ["light", "dark", "system"]

function read(): Theme {
  try {
    const t = localStorage.getItem("theme")
    if (t === "light" || t === "dark" || t === "system") return t
  } catch {
    /* storage unavailable */
  }
  return "system"
}

function apply(t: Theme) {
  const dark = t === "dark" || (t === "system" && matchMedia("(prefers-color-scheme: dark)").matches)
  document.documentElement.classList.toggle("dark", dark)
}

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(read)
  useEffect(() => {
    apply(theme)
    try {
      localStorage.setItem("theme", theme)
    } catch {
      /* ignore */
    }
    if (theme !== "system") return
    const mq = matchMedia("(prefers-color-scheme: dark)")
    const on = () => apply("system")
    mq.addEventListener("change", on)
    return () => mq.removeEventListener("change", on)
  }, [theme])
  return [theme, setTheme] as const
}

const ICON = { light: Sun, dark: Moon, system: Monitor }
const LABEL = { light: "Light", dark: "Dark", system: "System" }

/** One button that cycles light, dark, system. */
export function ThemeToggle() {
  const [theme, setTheme] = useTheme()
  const Icon = ICON[theme]
  const next = ORDER[(ORDER.indexOf(theme) + 1) % ORDER.length]
  return (
    <Button variant="ghost" size="sm" onClick={() => setTheme(next)} aria-label={`Theme: ${LABEL[theme]}. Switch to ${LABEL[next]}`} title={`Theme: ${LABEL[theme]}`}>
      <Icon aria-hidden />
      <span className="hidden lg:inline">{LABEL[theme]}</span>
    </Button>
  )
}
