import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ExternalLink } from 'lucide-react'

import { api } from '@/lib/api'
import { Card, CardHeader } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Tag } from '@/components/ui/Tag'
import { useToast } from '@/components/ui/Toast'

/**
 * One Google login for the numbers only the channel owner can see: YouTube
 * Analytics, thumbnail impressions + CTR, and Search Console.
 *
 * Google only redirects to localhost or https, and a homelab dashboard is usually
 * neither — so the consent tab may end on a "can't connect" page with the code in
 * its address bar. Pasting that address back here finishes the sign-in; when the
 * redirect does reach Plutus it finishes by itself and the status poll notices.
 *
 * The API never returns a token — only which channel is connected.
 */
interface GoogleStatus {
  client_configured: boolean
  connected: boolean
  channel_title: string
  channel_id: string
  connected_at: number
  missing_scopes: string[]
  redirect_uri: string
  reach_job_created: string
  reach_job_error: string
}

export function GoogleAccountSection() {
  const toast = useToast()
  const qc = useQueryClient()
  const [pending, setPending] = useState<{ auth_url: string; redirect_uri: string } | null>(null)
  const [pasted, setPasted] = useState('')
  const [busy, setBusy] = useState('')

  const q = useQuery({
    queryKey: ['google-status'],
    queryFn: () => api.get<GoogleStatus>('/api/v1/google/status'),
    // While a sign-in is open in another tab, watch for the callback finishing it.
    refetchInterval: pending ? 2500 : false,
  })
  const s = q.data
  useEffect(() => {
    if (s?.connected) setPending(null)
  }, [s?.connected])

  async function call<T>(key: string, fn: () => Promise<T>, ok?: string): Promise<T | undefined> {
    setBusy(key)
    try {
      const r = await fn()
      if (ok) toast.success(ok)
      qc.invalidateQueries({ queryKey: ['google-status'] })
      return r
    } catch (e) {
      toast.error(String(e))
      return undefined
    } finally {
      setBusy('')
    }
  }

  async function connect() {
    // Opened synchronously inside the click, then pointed at Google once the URL
    // exists — opening it after the await would be eaten by popup blockers.
    const tab = window.open('', '_blank')
    const r = await call('start', () =>
      api.post<{ auth_url: string; redirect_uri: string }>('/api/v1/google/start'),
    )
    if (!r) {
      tab?.close()
      return
    }
    if (tab) tab.location.href = r.auth_url
    setPending(r)
    setPasted('')
  }

  async function finish() {
    const r = await call(
      'finish',
      () => api.post<GoogleStatus>('/api/v1/google/finish', { response: pasted.trim() }),
      'Google account connected.',
    )
    if (r) setPending(null)
  }

  return (
    <Card>
      <CardHeader
        title="Google account"
        subtitle="Your channel's real numbers — YouTube Analytics, impressions & CTR, Search Console"
        action={
          s?.connected ? (
            <Button
              variant="danger"
              size="sm"
              disabled={busy === 'disconnect'}
              onClick={() => {
                if (confirm('Disconnect the Google account? The analytics tools stop working until you connect again.'))
                  call('disconnect', () => api.post('/api/v1/google/disconnect'), 'Disconnected.')
              }}
            >
              Disconnect
            </Button>
          ) : s?.client_configured ? (
            <Button variant="primary" size="sm" disabled={busy === 'start'} onClick={connect}>
              {pending ? 'Start again' : 'Connect'}
            </Button>
          ) : null
        }
      />
      <div className="space-y-2.5 px-4 pb-3.5 text-[12.5px]">
        {s && !s.client_configured && (
          <div className="space-y-1.5 text-ink-3">
            <p>
              First add an OAuth client on the{' '}
              <span className="text-ink-2">YouTube Studio &amp; Search Console</span> card under
              Connections. In{' '}
              <a
                className="text-accent hover:underline"
                href="https://console.cloud.google.com/apis/credentials"
                target="_blank"
                rel="noreferrer noopener"
              >
                Google Cloud Console
              </a>
              :
            </p>
            <ol className="list-decimal space-y-0.5 pl-5">
              <li>
                Enable YouTube Data API v3, YouTube Analytics API, YouTube Reporting API and Google
                Search Console API.
              </li>
              <li>
                OAuth consent screen: add yourself as a user and set it to{' '}
                <span className="text-ink-2">In production</span> — in Testing, Google expires the
                login every 7 days.
              </li>
              <li>
                Create an OAuth client of type <span className="text-ink-2">Desktop app</span> and
                paste its id and secret on the card.
              </li>
            </ol>
          </div>
        )}

        {s?.connected && (
          <div className="space-y-1.5">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-[13px] text-ink">{s.channel_title || 'Connected (no YouTube channel on this account)'}</span>
              <Tag>connected</Tag>
              {s.connected_at > 0 && (
                <span className="text-[11.5px] text-ink-3">
                  since {new Date(s.connected_at * 1000).toLocaleDateString()}
                </span>
              )}
            </div>
            {s.missing_scopes.length > 0 && (
              <p className="text-warn">
                Not granted: {s.missing_scopes.join(', ')}. Disconnect and connect again, leaving every
                box ticked on Google's consent page.
              </p>
            )}
            {s.reach_job_created ? (
              <p className="text-ink-3">
                Impressions &amp; CTR report registered with YouTube ({s.reach_job_created}). The first
                daily files arrive about 48 hours after that, with the 30 days before it backfilled.
              </p>
            ) : (
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-ink-3">
                  {s.reach_job_error
                    ? `The impressions report could not be set up: ${s.reach_job_error}`
                    : 'The impressions & CTR report is not set up yet.'}
                </span>
                <Button
                  size="sm"
                  disabled={busy === 'reach'}
                  onClick={() =>
                    call('reach', () => api.post('/api/v1/google/reach-job'), 'Impressions report set up.')
                  }
                >
                  Set up impressions reports
                </Button>
              </div>
            )}
          </div>
        )}

        {s?.client_configured && !s.connected && !pending && (
          <p className="text-ink-3">
            Not connected. Connect opens Google's consent page in a new tab; sign in with the account
            that owns your channel.
          </p>
        )}

        {pending && !s?.connected && (
          <div className="space-y-2 rounded-[var(--radius-sm)] border border-border bg-surface-2 p-3">
            <p className="text-ink-3">
              Approve access in the new tab (
              <a
                className="inline-flex items-center gap-0.5 text-accent hover:underline"
                href={pending.auth_url}
                target="_blank"
                rel="noreferrer noopener"
              >
                reopen it <ExternalLink size={11} />
              </a>
              ). Google then sends you to <code className="text-ink-2">{pending.redirect_uri}</code>. If
              that page says “Connected”, you are done. If the browser can't open it, copy the whole
              address from the address bar and paste it here:
            </p>
            <div className="flex gap-2">
              <Input
                value={pasted}
                onChange={(e) => setPasted(e.target.value)}
                placeholder="http://localhost:8766/api/v1/google/callback?state=…&code=…"
                aria-label="Address Google redirected to"
              />
              <Button
                variant="primary"
                size="sm"
                disabled={!pasted.trim() || busy === 'finish'}
                onClick={finish}
              >
                Finish
              </Button>
            </div>
          </div>
        )}
      </div>
    </Card>
  )
}
