import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ExternalLink } from 'lucide-react'

import { api } from '@/lib/api'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Modal } from '@/components/ui/Modal'
import { useToast } from '@/components/ui/Toast'

/**
 * Signing a provider account in from the browser.
 *
 * core/provider_login drives the CLI on a pty, captures the URL it prints and
 * feeds the code back — a complete flow with four endpoints that nothing called.
 * Settings offered a command to copy instead, which on a headless box means
 * finding a terminal, `docker exec`-ing into the container, and pasting there.
 *
 * The pty is the catch: it does not exist on Windows, so the server reports
 * whether the flow is usable at all and the entry point only appears when it is.
 */
interface Snapshot {
  state: 'idle' | 'starting' | 'awaiting_code' | 'finishing' | 'done' | 'failed'
  provider: string
  account_id: string
  url: string
  error: string
  output_tail: string
  token_captured: boolean
  available: boolean
  elapsed: number
}

export function GuidedLogin({
  provider,
  accountId,
  accountLabel,
  providerLabel,
  onClose,
  onDone,
}: {
  provider: string
  accountId: string
  accountLabel: string
  providerLabel: string
  onClose: () => void
  onDone: () => void
}) {
  const toast = useToast()
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [started, setStarted] = useState(false)

  const snap = useQuery({
    queryKey: ['provider-login'],
    queryFn: () => api.get<Snapshot>('/api/v1/providers/login'),
    // The CLI prints its URL a second or two in, and the code exchange takes
    // another few — poll throughout, including while the tab is not focused,
    // since you are about to go to another tab to authorise.
    refetchInterval: 1200,
    refetchIntervalInBackground: true,
  })
  const s = snap.data
  const mine = s && s.provider === provider && s.account_id === accountId

  useEffect(() => {
    if (!started || !mine) return
    if (s?.state === 'done') {
      toast.success(`${accountLabel} signed in.`)
      onDone()
    } else if (s?.state === 'failed') {
      toast.error(s.error || 'Login failed.')
    }
  }, [s?.state, started, mine])   // eslint-disable-line react-hooks/exhaustive-deps

  async function begin() {
    setBusy(true)
    try {
      await api.post(
        `/api/v1/providers/${provider}/accounts/${accountId}/login/start?use_token_flow=false`,
      )
      setStarted(true)
      snap.refetch()
    } catch (e) {
      toast.error(String(e))
    } finally {
      setBusy(false)
    }
  }

  async function sendCode() {
    setBusy(true)
    try {
      await api.post('/api/v1/providers/login/code', { code: code.trim() })
      setCode('')
      snap.refetch()
    } catch (e) {
      toast.error(String(e))
    } finally {
      setBusy(false)
    }
  }

  async function stop() {
    try {
      await api.post('/api/v1/providers/login/cancel')
    } catch {
      /* closing is the point; a failed cancel must not trap the dialog */
    }
    onClose()
  }

  const state = mine ? s?.state : 'idle'
  const running = state === 'starting' || state === 'awaiting_code' || state === 'finishing'

  return (
    <Modal open onClose={running ? stop : onClose} title={`Sign in — ${accountLabel}`}>
      <div className="space-y-3">
        <p className="text-[12.5px] text-ink-2">
          Plutus runs {providerLabel}'s own login and hands you the link it prints. Nothing
          is typed into Plutus except the code {providerLabel} gives you back.
        </p>

        {s && !s.available && (
          <p className="rounded-[var(--radius-sm)] border border-warn/40 bg-warn/5 px-2.5 py-2 text-[12.5px] text-ink-2">
            This server cannot drive an interactive login — it needs a pty, which
            Windows does not provide. Use the command shown on the account instead.
          </p>
        )}

        {state === 'idle' && s?.available && (
          <Button variant="primary" size="sm" disabled={busy} onClick={begin}>
            Start login
          </Button>
        )}

        {state === 'starting' && (
          <p className="text-[12.5px] text-ink-3">Starting {providerLabel}…</p>
        )}

        {state === 'awaiting_code' && (
          <div className="space-y-2.5">
            {s?.url ? (
              <a
                className="inline-flex items-center gap-1.5 text-[12.5px] text-accent hover:underline"
                href={s.url}
                target="_blank"
                rel="noreferrer noopener"
              >
                <ExternalLink size={13} /> Open {providerLabel} to authorise
              </a>
            ) : (
              <p className="text-[12.5px] text-ink-3">
                Waiting for {providerLabel} to print its link…
              </p>
            )}
            <div className="flex items-end gap-2">
              <div className="flex-1">
                <label className="mb-1 block text-[12px] text-ink-2" htmlFor="login-code">
                  Paste the code it gives you
                </label>
                <Input
                  id="login-code"
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && code.trim() && sendCode()}
                  placeholder="code from the browser"
                />
              </div>
              <Button variant="primary" size="sm" disabled={!code.trim() || busy} onClick={sendCode}>
                Submit
              </Button>
            </div>
          </div>
        )}

        {state === 'finishing' && (
          <p className="text-[12.5px] text-ink-3">Finishing…</p>
        )}

        {state === 'failed' && (
          <p className="rounded-[var(--radius-sm)] border border-danger/40 bg-danger/5 px-2.5 py-2 text-[12.5px] text-danger">
            {s?.error || 'Login failed.'}
          </p>
        )}

        {/* The CLI's own output. Diagnostic, capped server-side, and the only way
            to tell "waiting for you" from "quietly broken". */}
        {mine && s?.output_tail && state !== 'idle' && (
          <details>
            <summary className="cursor-pointer text-[11.5px] text-ink-3 hover:text-ink-2">
              {providerLabel} output
            </summary>
            <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-[var(--radius-sm)] bg-surface-2 px-2 py-1.5 font-mono text-[11px] leading-snug text-ink-2">
              {s.output_tail}
            </pre>
          </details>
        )}

        <div className="flex items-center gap-2 border-t border-border pt-3">
          <Button variant="ghost" size="sm" onClick={running ? stop : onClose}>
            {running ? 'Cancel login' : 'Close'}
          </Button>
          {running && s && (
            <span className="text-[11px] text-ink-3">{s.elapsed}s</span>
          )}
        </div>
      </div>
    </Modal>
  )
}
