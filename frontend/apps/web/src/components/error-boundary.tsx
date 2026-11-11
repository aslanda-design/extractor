import { Component, type ReactNode } from "react"

interface Props {
  children: ReactNode
  /** what to tell the person; the error itself goes to the console */
  label: string
}

/** Keeps one broken panel from taking the whole page down with it. */
export class ErrorBoundary extends Component<Props, { failed: boolean }> {
  state = { failed: false }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  componentDidCatch(error: unknown) {
    console.error("[ui]", error)
  }

  render() {
    if (!this.state.failed) return this.props.children
    return (
      <div role="alert" className="text-muted-foreground flex w-full flex-col items-start gap-2 p-6 text-sm">
        <p>{this.props.label}</p>
        <button type="button" className="text-primary text-xs underline underline-offset-4" onClick={() => this.setState({ failed: false })}>
          Try again
        </button>
      </div>
    )
  }
}
