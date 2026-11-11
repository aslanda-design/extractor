import { cn } from "@workspace/ui/lib/utils"

export type RightView = "results" | "pipeline"

/** Switches the right-hand panel between the extracted fields and the live pipeline graph. */
export function ViewToggle({ value, onChange, pipelineAvailable }: { value: RightView; onChange: (v: RightView) => void; pipelineAvailable: boolean }) {
  return (
    <div role="group" aria-label="Right panel view" className="bg-secondary ml-auto flex rounded-md p-0.5">
      {(["results", "pipeline"] as const).map((v) => (
        <button
          key={v}
          type="button"
          aria-pressed={value === v}
          disabled={v === "pipeline" && !pipelineAvailable}
          onClick={() => onChange(v)}
          className={cn(
            "rounded px-2.5 py-1 text-xs font-medium transition-colors disabled:opacity-40",
            value === v ? "bg-card text-foreground shadow-[0_1px_2px_rgb(0_0_0/0.2)]" : "text-muted-foreground hover:text-foreground"
          )}
        >
          {v === "results" ? "Results" : "Pipeline"}
        </button>
      ))}
    </div>
  )
}
