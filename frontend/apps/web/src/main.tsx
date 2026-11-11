import { StrictMode, useEffect, useState } from "react"
import { createRoot } from "react-dom/client"

import "@workspace/ui/globals.css"
import { App } from "./App.tsx"
import { DevApp } from "./dev/dev-app.tsx"
import { TooltipProvider } from "@workspace/ui/components/tooltip"

/** Hash routing: `#/dev` is the engine test bench, anything else is the review screen. */
function Root() {
  const [hash, setHash] = useState(location.hash)
  useEffect(() => {
    const on = () => setHash(location.hash)
    addEventListener("hashchange", on)
    return () => removeEventListener("hashchange", on)
  }, [])
  return hash.startsWith("#/dev") ? <DevApp /> : <App />
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <TooltipProvider>
      <Root />
    </TooltipProvider>
  </StrictMode>
)
