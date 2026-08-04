/**
 * Catches a render error so one broken card does not take the app with it.
 *
 * There was no boundary anywhere in the tree, and React's behaviour without one
 * is not "render the rest" — it unmounts the whole root. So a single component
 * reading a field that a service did not return left a blank white page, with
 * the reason only in the browser console. That is the wrong failure for a
 * dashboard whose entire job is talking to a dozen self-hosted services that go
 * down, get upgraded, and change their JSON.
 *
 * Two placements, because they fail differently:
 *
 * - **Around the page**, keyed by route, so the shell and its navigation survive
 *   and you can click away to a page that works. The key is what makes leaving
 *   the broken page reset it — without it the boundary stays latched and every
 *   later route renders the error instead.
 * - **Around the whole app** as a backstop, for a throw in the shell itself.
 *
 * The panel shows the actual message rather than "something went wrong". On a
 * self-hosted box the person looking at this is the person who can fix it, and
 * "Cannot read properties of undefined (reading 'categories')" tells them which
 * endpoint to go and look at.
 */
import { Component, type ErrorInfo, type ReactNode } from 'react'
import { RotateCw, TriangleAlert } from 'lucide-react'

import { Button } from '@/components/ui/Button'
import { Card } from '@/components/ui/Card'

interface Props {
  children: ReactNode
  /** Named in the message, so "Rooms could not be displayed" beats "an error". */
  label?: string
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
    // Kept: the boundary swallows the throw, and without this the stack that
    // actually names the component is gone.
    console.error('Render error', this.props.label ?? '', error, info.componentStack)
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children

    const what = this.props.label ? `${this.props.label} could not be displayed` : 'Something broke'
    return (
      <div className="p-4">
        <Card className="p-4">
          <div className="flex items-start gap-3">
            <TriangleAlert size={18} className="mt-0.5 shrink-0 text-danger" aria-hidden />
            <div className="min-w-0 flex-1">
              <h2 className="text-[14px] font-semibold text-ink">{what}</h2>
              <p className="mt-1 text-[12.5px] leading-snug text-ink-2">
                The rest of Plutus is still working — use the sidebar to go somewhere else.
                This is usually a service returning something unexpected.
              </p>
              <pre className="mt-2 max-h-40 overflow-auto rounded-[var(--radius-sm)] bg-surface-2 p-2 text-[11.5px] leading-snug text-ink-2">
                {error.message || String(error)}
              </pre>
              <Button
                size="sm"
                className="mt-3"
                onClick={() => this.setState({ error: null })}
              >
                <RotateCw size={13} /> Try again
              </Button>
            </div>
          </div>
        </Card>
      </div>
    )
  }
}
