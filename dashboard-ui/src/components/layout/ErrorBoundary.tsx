import { Component, type ErrorInfo, type ReactNode } from 'react'

interface Props {
  children: ReactNode
  fallback?: ReactNode
}
interface State {
  error: Error | null
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('[ErrorBoundary] Uncaught error:', error, info.componentStack)
  }

  render() {
    if (this.state.error) {
      return (
        this.props.fallback ?? (
          <div className="p-6 rounded-xl border border-[var(--color-red)]/40 bg-[var(--color-red)]/10 text-[var(--color-red)]">
            <div className="font-mono text-sm font-semibold mb-1">Component Error</div>
            <div className="font-mono text-xs text-[var(--color-text-muted)]">
              {this.state.error.message}
            </div>
          </div>
        )
      )
    }
    return this.props.children
  }
}
