import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Check, X, Loader2, Plus } from 'lucide-react'
import { api } from '@/lib/api'
import { Card, CardHeader } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { useToast } from '@/components/ui/Toast'

interface Account {
  id: string
  label: string
  authenticated: boolean
  state: string
  config_dir: string
}
interface Provider {
  id: string
  label: string
  runnable: boolean
  state: string
  login_command: string
  cli: { installed: boolean; path: string; version: string; install_hint: string }
  accounts: Account[]
}
interface Check {
  name: string
  ok: boolean
  detail: string
}

const STATE_TEXT: Record<string, string> = {
  connected: 'Connected',
  login_required: 'Login required',
  no_accounts: 'No accounts',
  cli_missing: 'CLI not installed',
}

export function AiProvidersSection() {
  const toast = useToast()
  const { data, refetch } = useQuery({
    queryKey: ['ai-providers'],
    queryFn: () => api.get<{ providers: Provider[]; guided_login_available: boolean }>('/api/v1/providers'),
  })
  const [newLabel, setNewLabel] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState('')
  const [adding, setAdding] = useState('')
  const [checks, setChecks] = useState<Record<string, Check[]>>({})

  async function addAccount(pid: string) {
    const label = (newLabel[pid] || '').trim()
    if (!label) return
    setBusy(pid)
    try {
      await api.post(`/api/v1/providers/${pid}/accounts`, { label })
      setNewLabel((v) => ({ ...v, [pid]: '' }))
      setAdding('')
      refetch()
    } catch (e) {
      toast.error(String(e))
    } finally {
      setBusy('')
    }
  }

  async function act(path: string, ok: string) {
    setBusy(path)
    try {
      await api.post(path)
      toast.success(ok)
      refetch()
    } catch (e) {
      toast.error(String(e))
    } finally {
      setBusy('')
    }
  }

  async function test(pid: string, aid: string) {
    const key = `${pid}/${aid}`
    setBusy(key)
    try {
      const r = await api.post<{ ok: boolean; checks: Check[] }>(
        `/api/v1/providers/${pid}/accounts/${aid}/test?with_mcp=true`,
      )
      setChecks((c) => ({ ...c, [key]: r.checks }))
      if (r.ok) toast.success('All capability checks passed.')
    } catch (e) {
      toast.error(String(e))
    } finally {
      setBusy('')
    }
  }

  async function removeAccount(pid: string, aid: string, label: string) {
    if (!confirm(`Remove account “${label}”? Its stored login is deleted.`)) return
    setBusy(`${pid}/${aid}`)
    try {
      await api.del(`/api/v1/providers/${pid}/accounts/${aid}`)
      refetch()
    } catch (e) {
      toast.error(String(e))
    } finally {
      setBusy('')
    }
  }

  return (
    <Card>
      <CardHeader
        title="AI providers"
        subtitle="Agents run through an authenticated CLI, not an API key. Each account keeps its own login."
      />
      <div className="space-y-4 px-4 pb-4">
        {(data?.providers ?? []).map((p) => (
          <div key={p.id} className="rounded-[var(--radius-md)] border border-border p-3">
            <div className="flex flex-wrap items-center gap-2">
              <strong className="text-[13px] text-ink">{p.label}</strong>
              <span
                className={
                  'rounded px-1.5 py-0.5 text-[11px] ' +
                  (p.state === 'connected'
                    ? 'bg-ok/15 text-ok'
                    : p.state === 'cli_missing'
                      ? 'bg-danger/15 text-danger'
                      : 'bg-surface-2 text-ink-3')
                }
              >
                {STATE_TEXT[p.state] ?? p.state}
              </span>
              {p.cli.version && <span className="text-[11.5px] text-ink-3">{p.cli.version}</span>}
              {!p.runnable && (
                <span className="text-[11.5px] text-ink-3">· detection only — agents can’t use this yet</span>
              )}
            </div>

            {!p.cli.installed && (
              <p className="mt-2 text-[12px] text-ink-3">
                Not found in the container. Install with <code className="text-ink-2">{p.cli.install_hint}</code>
              </p>
            )}

            <div className="mt-2 space-y-1.5">
              {p.accounts.map((a) => {
                const key = `${p.id}/${a.id}`
                return (
                  <div key={a.id} className="rounded-[var(--radius-sm)] bg-surface-2 px-2.5 py-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-[12.5px] text-ink">{a.label}</span>
                      <span className={'text-[11.5px] ' + (a.authenticated ? 'text-ok' : 'text-ink-3')}>
                        {a.authenticated ? 'linked' : 'not linked'}
                      </span>
                      <div className="ml-auto flex items-center gap-1">
                        <Button variant="ghost" size="sm" disabled={busy === key} onClick={() => test(p.id, a.id)}>
                          {busy === key ? <Loader2 size={13} className="animate-spin" /> : 'Test'}
                        </Button>
                        {a.authenticated && (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => act(`/api/v1/providers/${p.id}/accounts/${a.id}/logout`, 'Logged out.')}
                          >
                            Logout
                          </Button>
                        )}
                        <Button variant="ghost" size="sm" onClick={() => removeAccount(p.id, a.id, a.label)}>
                          Remove
                        </Button>
                      </div>
                    </div>

                    {!a.authenticated && p.cli.installed && (
                      <div className="mt-1.5">
                        <p className="text-[11.5px] text-ink-3">Link this account by running:</p>
                        <code className="mt-1 block overflow-x-auto whitespace-pre rounded bg-surface px-2 py-1 text-[11px] text-ink-2">
                          {`docker exec -it -e CLAUDE_CONFIG_DIR=${a.config_dir} plutus-mcp claude`}
                        </code>
                      </div>
                    )}

                    {checks[key] && (
                      <ul className="mt-2 space-y-1">
                        {checks[key].map((c) => (
                          <li key={c.name} className="flex items-start gap-1.5 text-[11.5px]">
                            {c.ok ? (
                              <Check size={13} className="mt-0.5 shrink-0 text-ok" />
                            ) : (
                              <X size={13} className="mt-0.5 shrink-0 text-danger" />
                            )}
                            <span className="text-ink-2">
                              {c.name}
                              {c.detail && <span className="text-ink-3"> — {c.detail}</span>}
                            </span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                )
              })}
            </div>

            {adding === p.id ? (
              <div className="mt-2 flex items-center gap-2">
                <Input
                  autoFocus
                  className="max-w-[240px]"
                  value={newLabel[p.id] ?? ''}
                  placeholder="Account name, e.g. Personal Pro"
                  onChange={(e) => setNewLabel((v) => ({ ...v, [p.id]: e.target.value }))}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') addAccount(p.id)
                    if (e.key === 'Escape') setAdding('')
                  }}
                />
                <Button variant="primary" size="sm" disabled={busy === p.id} onClick={() => addAccount(p.id)}>
                  Add
                </Button>
                <Button variant="ghost" size="sm" onClick={() => setAdding('')}>
                  Cancel
                </Button>
              </div>
            ) : (
              <Button variant="ghost" size="sm" className="mt-2" onClick={() => setAdding(p.id)}>
                <Plus size={13} className="mr-1" /> Add {p.label} account
              </Button>
            )}
          </div>
        ))}
      </div>
    </Card>
  )
}
