import { useState } from 'react'
import { Check, X, Loader2, Plus } from 'lucide-react'
import { api } from '@/lib/api'
import { Card, CardHeader } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { useToast } from '@/components/ui/Toast'
import { useProviders, type ProviderAccount as Account } from '@/lib/providers'

interface Check {
  name: string
  ok: boolean
  detail: string
}
interface UsageItem {
  label: string
  value: string
  hint?: string
}
interface Usage {
  ok: boolean
  supported: boolean
  items: UsageItem[]
  error?: string
}

/** Spend and limits for one account.
 *
 *  Most of these are free plans, so "how much is left" is the question that gets
 *  asked. Only some providers answer it; the ones that do not say why, because a
 *  blank panel reads as broken rather than as "not published". */
function UsagePanel({ usage }: { usage: Usage }) {
  if (!usage.supported) {
    return <p className="mt-1.5 text-[11.5px] text-ink-3">{usage.error}</p>
  }
  if (!usage.ok) {
    return <p className="mt-1.5 text-[11.5px] text-danger">{usage.error}</p>
  }
  return (
    <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1">
      {usage.items.map((it) => (
        <span key={it.label} className="text-[11.5px]">
          <span className="text-ink-3">{it.label} </span>
          <strong className="text-ink">{it.value}</strong>
          {it.hint && <span className="text-ink-3"> {it.hint}</span>}
        </span>
      ))}
    </div>
  )
}

const STATE_TEXT: Record<string, string> = {
  connected: 'Connected',
  adoptable: 'Login found — adopt it',
  login_required: 'Login required',
  key_required: 'API key required',
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

/** The key field. For an HTTP provider this is the *only* way to link an account,
 *  so it is not tucked under a log-in command that does not exist. */
function KeyField({
  account,
  primary,
  draft,
  setDraft,
  onSave,
}: {
  account: Account
  primary: boolean
  draft: string
  setDraft: (v: string) => void
  onSave: (key: string) => void
}) {
  const stored = account.auth_kind === 'api_key'
  return (
    <div className={primary ? '' : 'mt-1.5'}>
      <p className="text-[11.5px] text-ink-3">
        {stored
          ? 'An API key is stored for this account.'
          : primary
            ? `Paste an API key to link this account. ${account.key_hint}`
            : `Or paste an API key — simplest for headless use. ${account.key_hint}`}
      </p>
      <div className="mt-1 flex items-center gap-2">
        <Input
          className="flex-1"
          type="password"
          placeholder={stored ? '•••••• stored — paste to replace' : 'API key'}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && draft.trim()) onSave(draft.trim())
          }}
        />
        <Button variant={primary && !stored ? 'primary' : 'default'} size="sm" disabled={!draft.trim()} onClick={() => onSave(draft.trim())}>
          Save key
        </Button>
      </div>
    </div>
  )
}

export function AiProvidersSection() {
  const toast = useToast()
  const { data, refetch } = useProviders()
  const [newLabel, setNewLabel] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState('')
  const [adding, setAdding] = useState('')
  const [checks, setChecks] = useState<Record<string, Check[]>>({})
  const [keyDraft, setKeyDraft] = useState<Record<string, string>>({})
  const [usage, setUsage] = useState<Record<string, Usage>>({})

  async function loadUsage(pid: string, aid: string) {
    const key = `${pid}/${aid}`
    setBusy(`usage-${key}`)
    try {
      const got = await api.get<Usage>(`/api/v1/providers/${pid}/accounts/${aid}/usage`)
      setUsage((u) => ({ ...u, [key]: got }))
    } catch (e) {
      toast.error(String(e))
    } finally {
      setBusy('')
    }
  }

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

  async function act(path: string, ok: string, body?: unknown) {
    setBusy(path)
    try {
      await api.post(path, body)
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
        subtitle="Claude Code and Codex run as authenticated CLIs; Gemini runs on a free API key. Each account keeps its own credential."
      />
      <div className="space-y-4 px-4 pb-4">
        {(data?.providers ?? []).map((p) => {
          const isApi = p.kind === 'api'
          return (
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
              {!isApi && p.cli.version && <span className="text-[11.5px] text-ink-3">{p.cli.version}</span>}
              {isApi && <span className="text-[11.5px] text-ink-3">API key · no CLI needed</span>}
              {p.role_label && <span className="text-[11.5px] text-ink-3">· {p.role_label}</span>}
              {!p.runnable && (
                <span className="text-[11.5px] text-ink-3">· detection only — agents can’t use this yet</span>
              )}
            </div>

            <div className="mt-2 space-y-1">
              {/* An HTTP provider has nothing to install and nothing to log into.
                  Showing an install hint and a login command for it sent people
                  to a terminal to fix something the key field already solves. */}
              {!isApi && (
                <Cmd
                  label={p.cli.installed ? 'Install / update' : 'Not in the container — install with'}
                  cmd={p.cli.install_hint}
                  tone={p.cli.installed ? 'muted' : 'warn'}
                />
              )}
              {p.accounts.length === 0 && (
                <p className="text-[11px] text-ink-3">
                  {isApi
                    ? `Add an account below, then paste a key. ${p.key_hint}`
                    : 'Add an account below to get its log-in command.'}
                </p>
              )}
            </div>

            <div className="mt-2 space-y-1.5">
              {p.accounts.map((a) => {
                const key = `${p.id}/${a.id}`
                return (
                  <div key={a.id} className="rounded-[var(--radius-sm)] bg-surface-2 px-2.5 py-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <button
                        className="text-left text-[12.5px] text-ink hover:underline"
                        title="Rename this account"
                        onClick={async () => {
                          const next = prompt('Account name', a.label)?.trim()
                          if (!next || next === a.label) return
                          await act(`/api/v1/providers/${p.id}/accounts/${a.id}/rename`,
                                    'Renamed.', { label: next })
                        }}
                      >
                        {a.label}
                      </button>
                      <span className={'text-[11.5px] ' + (a.authenticated ? 'text-ok' : 'text-ink-3')}>
                        {a.authenticated ? 'linked' : 'not linked'}
                      </span>
                      {/* The single action that moves this account forward, placed
                          where it cannot be missed. It used to sit below the
                          log-in command, so the Test result said "use Adopt login"
                          while the button was out of sight. */}
                      {!a.authenticated && a.adoptable && (
                        <Button
                          variant="primary"
                          size="sm"
                          disabled={busy === `/api/v1/providers/${p.id}/accounts/${a.id}/adopt`}
                          onClick={() =>
                            act(`/api/v1/providers/${p.id}/accounts/${a.id}/adopt`, 'Login adopted.')
                          }
                        >
                          Adopt login
                        </Button>
                      )}
                      <div className="ml-auto flex items-center gap-1">
                        {a.authenticated && (
                          <Button
                            variant="ghost"
                            size="sm"
                            title="Spend and limits for this account"
                            disabled={busy === `usage-${key}`}
                            onClick={() => loadUsage(p.id, a.id)}
                          >
                            {busy === `usage-${key}` ? (
                              <Loader2 size={13} className="animate-spin" />
                            ) : (
                              'Usage'
                            )}
                          </Button>
                        )}
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

                    {/* An HTTP provider links with a key and nothing else, so the
                        key field is the account's primary action rather than an
                        afterthought under a log-in command it does not have. */}
                    {isApi && a.accepts_key && (
                      <div className="mt-1.5">
                        <KeyField
                          account={a}
                          primary
                          draft={keyDraft[a.id] ?? ''}
                          setDraft={(v) => setKeyDraft((d) => ({ ...d, [a.id]: v }))}
                          onSave={(k) =>
                            act(`/api/v1/providers/${p.id}/accounts/${a.id}/token`, 'Key saved.', {
                              token: k,
                            }).then(() => setKeyDraft((d) => ({ ...d, [a.id]: '' })))
                          }
                        />
                      </div>
                    )}

                    {/* Always shown for a CLI. Gating this on cli.installed hid the
                        log-in command exactly when detection was wrong or the CLI had
                        been wiped by an update — leaving no way forward. */}
                    {!isApi && a.login_command && (
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
                        {a.accepts_key && (
                          <KeyField
                            account={a}
                            primary={false}
                            draft={keyDraft[a.id] ?? ''}
                            setDraft={(v) => setKeyDraft((d) => ({ ...d, [a.id]: v }))}
                            onSave={(k) =>
                              act(`/api/v1/providers/${p.id}/accounts/${a.id}/token`, 'Key saved.', {
                                token: k,
                              }).then(() => setKeyDraft((d) => ({ ...d, [a.id]: '' })))
                            }
                          />
                        )}
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

                    {usage[key] && <UsagePanel usage={usage[key]} />}

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
          )
        })}
      </div>
    </Card>
  )
}
