import { Component, type ErrorInfo, type ReactNode } from "react"
import { Button } from "./ui"

type Props = { children: ReactNode; fallbackTitle?: string }
type State = { error: Error | null }

// Global/route-level error boundary. A render-time crash in any child is caught
// here so the whole app never white-screens. The error is logged (so an error
// tracker like Sentry captures it) rather than being silently swallowed.
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Intentional logging — surfaces the crash to console/error tracking.
    console.error("ErrorBoundary caught an error:", error, info.componentStack)
  }

  private reset = (): void => this.setState({ error: null })

  render(): ReactNode {
    const { error } = this.state
    if (!error) return this.props.children
    return (
      <div role="alert" className="grid min-h-[40vh] place-items-center px-4">
        <div className="card max-w-md p-6 text-center">
          <div className="mb-3 text-3xl">{"\u26A0\uFE0F"}</div>
          <h2 className="mb-1 text-lg font-extrabold text-fg">
            {this.props.fallbackTitle || "Something went wrong"}
          </h2>
          <p className="mb-4 text-sm text-muted">
            An unexpected error occurred while rendering this view. You can try again,
            or reload the page.
          </p>
          <div className="flex justify-center gap-2">
            <Button variant="ghost" onClick={this.reset}>
              Try again
            </Button>
            <Button onClick={() => window.location.reload()}>Reload</Button>
          </div>
        </div>
      </div>
    )
  }
}
