import { useState } from "react"
import { Check, Copy } from "lucide-react"

import { Button } from "@workspace/ui/components/button"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@workspace/ui/components/dialog"

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description: string
  text: string
  fileName: string
}

/** Shows YAML or JSON text with copy and download. */
export function TextDialog({ open, onOpenChange, title, description, text, fileName }: Props) {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      /* clipboard blocked: the text is still selectable */
    }
  }
  const download = () => {
    const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }))
    const a = document.createElement("a")
    a.href = url
    a.download = fileName
    a.click()
    URL.revokeObjectURL(url)
  }
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[85vh] flex-col sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <pre className="bg-secondary min-h-0 flex-1 overflow-auto rounded-md p-3 font-mono text-xs leading-relaxed" tabIndex={0}>
          {text}
        </pre>
        <div className="flex gap-2">
          <Button variant="outline" onClick={copy}>
            {copied ? <Check aria-hidden /> : <Copy aria-hidden />}
            {copied ? "Copied" : "Copy"}
          </Button>
          <Button variant="outline" onClick={download}>
            Download
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
