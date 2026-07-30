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
  login_command: string
  role_label: string
  isolated: boolean
  adoptable: boolean
  adoptable_from: string
}
interface Provider {
  id: string
  label: string
  runnable: boolean
  state: string
  login_command: string
  cli: { installed: boolean; path: string; version: string; install_hint: string }
  accounts: Account[]
  role_label: string
  isolated: boolean
}
interface Check {
  name: string
  ok: boolean
  detail: string
}

const STATE_TEXT: Record<string, string> = {
  connected: 'Connected',
  adoptable: 'Login found — adopt it',
  login_required: 'Login required',
  no_accounts: 'No accounts',
  cli_missing: 'CLI not installed',
}


/** A shell command the user is expected to run, with one-press copy. */
function Cmd({ label, cmd, tone = 'muted' }: { label: string; cmd: string; tone?: 'muted' | 'warn' | 'accent' }) {
  const [copied, setCopied] = useState(false)
  if (!cmd) return null
  const labelTone = tone === 'warn' ? 'text-danger' : tone === 'accent' ? 'text-accent' : 'text-ink-3'
  return (
    <div>
      <div className="flex items-center gap-2">
        <span className={`text-[11.5px] ${labelTone}`}>{label}</span>
        <button
          className="ml-auto rounded px-1.5 py-0.5 text-[11px] text-ink-3 hover:bg-surface-hover"
          onClick={() => {
            navigator.clipboard?.writeText(cmd)
            setCopied(true)
            setTimeout(() => setCopied(false), 1500)
          }}
        >
          {copied ? 'copied' : 'copy'}
        </button>
      </div>
      <code className="mt-0.5 block overflow-x-auto whitespace-pre rounded bg-surface px-2 py-1 text-[11px] text-ink-2">
        {cmd}
      </code>
    </div>
  )
}

export function AiProvidersSection() {
  const toast = useToast()
  const { data, refetch } = useQuery({
    queryKey: ['ai-providers'],
    queryFn: () => api.get<{ providers: Provider[]; guided_login_available: boolean }>('/api/v1/providers'),
    // A CLI installed or a login completed from a terminal has to show up without
    // a page reload — the card used to sit on cached data saying "not installed"
    // while Test reported the very same CLI as present.
    refetchInterval: 15000,
    refetchOnWindowFocus: true,
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
      refetch()
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
              {p.role_label && <span className="text-[11.5px] text-ink-3">· {p.role_label}</span>}
              {!p.runnable && (
                <span className="text-[11.5px] text-ink-3">· detection only — agents can’t use this yet</span>
              )}
            </div>

            <div className="mt-2 space-y-1">
              <Cmd
                label={p.cli.installed ? 'Install / update' : 'Not in the container — install with'}
                cmd={p.cli.install_hint}
                tone={p.cli.installed ? 'muted' : 'warn'}
              />
              {p.accounts.length === 0 && (
                <p className="text-[11px] text-ink-3">Add an account below to get its log-in command.</p>
              )}
            </div>

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

                    {/* Always shown. Gating this on cli.installed hid the log-in
                        command exactly when detection was wrong or the CLI had been
                        wiped by an update — leaving no way forward. */}
                    {a.login_command && (
                      <div className="mt-1.5">
                        <Cmd
                          label={
                            a.authenticated
                              ? 'Log in again / switch identity'
                              : a.isolated
                                ? 'Link this account by running'
                                : `${p.label} has no per-account config dir — log in once, then adopt`
                          }
                          cmd={a.login_command}
                          tone={a.authenticated ? 'muted' : 'accent'}
                        />
                        {a.adoptable ? (
                          <div className="mt-1.5 flex flex-wrap items-center gap-2">
                            <span className="text-[11.5px] text-ok">
                              Login found in {a.adoptable_from}
                            </span>
                            <Button
                              variant="primary"
                              size="sm"
                              disabled={busy === `adopt-${a.id}`}
                              onClick={() =>
                                act(`/api/v1/providers/${p.id}/accounts/${a.id}/adopt`, 'Login adopted.')
                              }
                            >
                              Adopt login
                            </Button>
                          </div>
                        ) : (
                          !a.isolated && (
                            <p className="mt-1 text-[11px] text-ink-3">
                              After logging in, come back and press Adopt login. For a second account, log out of
                              the CLI, log in as the other identity, then adopt into that account.
                            </p>
                          )
                        )}
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
