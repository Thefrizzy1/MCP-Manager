import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Square, Play, Pause, Trash2, Bot, CalendarClock, RotateCw } from 'lucide-react'
import { api } from '@/lib/api'
import { navigate } from '@/lib/router'
import type { Service } from '@/lib/health'
import { PageHead, PageBody } from '@/components/PageHead'
import { Card, CardHeader } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Input, Select, Textarea } from '@/components/ui/Input'
import { Field } from '@/components/ui/Field'
import { Stat } from '@/components/ui/Stat'
import { StatusDot } from '@/components/ui/StatusDot'
import { EmptyState } from '@/components/ui/EmptyState'
import { useToast } from '@/components/ui/Toast'
import { ConnectionPicker } from '@/components/agents/ConnectionPicker'
import { ModelPicker } from '@/components/agents/ModelPicker'
import { RunTranscriptModal } from '@/components/agents/RunTranscriptModal'
import { linkedAccounts as toLinked, splitAccount, useProviders } from '@/lib/providers'

interface AgentStatus {
  auth?: { mode?: string }
  running?: boolean
  current_label?: string
  runs_today?: number
  max_runs_per_day?: number
  queue_depth?: number
  total_cost_usd?: number
  linked_accounts?: number
}
interface Run {
  id?: string
  label?: string
  ok?: boolean
  error?: string
  result?: string
  cost_usd?: number
  started?: string
  cancelled?: boolean
}
interface RunPrefill {
  id?: string
  label: string
  prompt: string
  mcp_services: string[] | null
  provider: string
  account_id: string
  model?: string
}
interface Schedule {
  id: string
  name: string
  kind: string
  cron: string
  timezone?: string
  enabled: boolean
  next_run?: string | null
  payload?: Record<string, unknown>
}

function cronFrom(kind: string, time: string, dow: string, raw: string): string | null {
  const [hh, mm] = (time || '07:00').split(':').map((x) => parseInt(x, 10))
  if (kind === 'daily') return `${mm} ${hh} * * *`
  if (kind === 'weekly') return `${mm} ${hh} * * ${dow}`
  if (kind === 'cron') return raw.trim()
  return null
}

export function Agents() {
  const qc = useQueryClient()
  const toast = useToast()
  const status = useQuery({
    queryKey: ['agent-status'],
    queryFn: () => api.get<AgentStatus>('/api/v1/agent/status'),
    refetchInterval: 4000,
  })
  const runs = useQuery({
    queryKey: ['agent-runs'],
    queryFn: () => api.get<{ runs?: Run[] }>('/api/v1/agent/runs'),
    refetchInterval: 4000,
  })
  const schedules = useQuery({
    queryKey: ['schedules'],
    queryFn: () => api.get<{ schedules?: Schedule[] }>('/api/v1/schedules'),
  })
  const conns = useQuery({
    queryKey: ['agent-conns'],
    queryFn: () => api.get<{ services?: Service[] }>('/api/v1/dashboard?sections=services'),
  })

  const [wizard, setWizard] = useState(false)
  const [prefill, setPrefill] = useState<RunPrefill | null>(null)
  const [console, setConsole] = useState<string[]>([])
  const esRef = useRef<EventSource | null>(null)

  function startStream() {
    esRef.current?.close()
    setConsole([])
    const es = new EventSource('/api/v1/agent/stream')
    esRef.current = es
    es.onmessage = (ev) => {
      let line = ev.data
      try {
        line = JSON.parse(ev.data)
      } catch {
        /* keep raw */
      }
      setConsole((c) => [...c, String(line)])
    }
    es.addEventListener('end', () => {
      es.close()
      esRef.current = null
      status.refetch()
      runs.refetch()
    })
    es.onerror = () => {
      es.close()
      esRef.current = null
    }
  }
  useEffect(() => () => esRef.current?.close(), [])

  const [rerunning, setRerunning] = useState<string | null>(null)
  const [openRun, setOpenRun] = useState<string | null>(null)
  const s = status.data
  const cap = s?.max_runs_per_day ?? 0
  const used = s?.runs_today ?? 0
  const mode = s?.auth?.mode ?? 'none'
  const onPlan = mode === 'session_token' || mode === 'subscription'
  // auth.mode only knows about Claude. An install whose only linked account is
  // Codex or Gemini can run agents perfectly well and used to be met by a red
  // "no provider connected" banner regardless.
  const linkedCount = s?.linked_accounts ?? 0
  const canRun = onPlan || mode === 'api_key' || linkedCount > 0

  // Open the wizard prefilled from a past run, so the prompt, connections or
  // schedule can be adjusted before spending another run.
  async function rerun(id: string) {
    setRerunning(id)
    try {
      const rec = await api.get<RunPrefill>(`/api/v1/agent/runs/${encodeURIComponent(id)}`)
      setPrefill(rec)
      setWizard(true)
    } catch (e) {
      toast.error(String(e))
    } finally {
      setRerunning(null)
    }
  }

  async function clearRuns() {
    if (!confirm('Clear all agent run history? This removes old run records and resets the all-time cost.')) return
    try {
      await api.del('/api/v1/agent/runs')
      runs.refetch()
      status.refetch()
    } catch {
      /* ignore — nothing to clear */
    }
  }

  return (
    <>
      <PageHead
        title="Agents"
        subtitle="Launch, schedule and monitor headless agents"
        actions={
          <Button
            variant="primary"
            size="sm"
            onClick={() => {
              setPrefill(null)
              setWizard((w) => !w)
            }}
          >
            <Plus size={14} /> New agent
          </Button>
        }
      />
      <PageBody>
        <div className="space-y-5">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label="Runs today" value={cap ? `${used}/${cap}` : used} tone={cap && used >= cap ? 'danger' : 'muted'} />
            <Stat label="Queued" value={s?.queue_depth ?? 0} tone={(s?.queue_depth ?? 0) > 0 ? 'warn' : 'muted'} />
            <Stat label="Cost (all-time)" value={`$${Math.round((s?.total_cost_usd ?? 0) * 100) / 100}`} />
            <Stat
              label="Accounts"
              value={linkedCount > 0 ? linkedCount : onPlan ? 'Plan' : mode === 'api_key' ? 'API' : 'Off'}
              hint={
                linkedCount > 0
                  ? `${linkedCount === 1 ? 'account' : 'accounts'} linked`
                  : onPlan
                    ? 'connected'
                    : mode === 'api_key'
                      ? 'API billing'
                      : 'connect in Settings'
              }
              tone={canRun ? 'ok' : 'danger'}
              onClick={() => navigate('settings')}
            />
          </div>

          {status.isSuccess && !canRun && (
            <div className="flex flex-wrap items-center gap-3 rounded-[var(--radius-md)] border border-danger/30 bg-danger/5 px-4 py-3">
              <Bot size={16} className="text-danger" />
              <div className="min-w-0 flex-1">
                <p className="text-[13px] font-medium text-ink">No AI provider connected</p>
                <p className="text-[12px] text-ink-3">
                  Agents can’t run until one account is linked — a Claude Code or Codex login, or a
                  free Gemini API key.
                </p>
              </div>
              <Button variant="primary" size="sm" onClick={() => navigate('settings')}>
                Connect
              </Button>
            </div>
          )}

          {wizard && (
            <LaunchWizard
              key={prefill?.id ?? 'blank'}
              initial={prefill}
              connections={orderConnections(
                (conns.data?.services ?? []).filter((x) => x.configured && !x.ignored),
              )}
              onLaunched={() => {
                setWizard(false)
                setPrefill(null)
                startStream()
                status.refetch()
              }}
              onScheduled={() => {
                setWizard(false)
                setPrefill(null)
                qc.invalidateQueries({ queryKey: ['schedules'] })
              }}
            />
          )}

          <Card>
            <CardHeader
              title="Running & recent"
              action={
                <div className="flex items-center gap-1.5">
                  {s?.running && (
                    <Button variant="danger" size="sm" onClick={() => api.post('/api/v1/agent/cancel').then(() => status.refetch())}>
                      <Square size={13} /> Stop
                    </Button>
                  )}
                  {(runs.data?.runs ?? []).length > 0 && (
                    <Button variant="ghost" size="sm" onClick={clearRuns} title="Delete old run records and reset the all-time cost">
                      <Trash2 size={13} /> Clear
                    </Button>
                  )}
                </div>
              }
            />
            <div className="px-2 pb-2">
              {s?.running && (
                <div className="flex items-center gap-2.5 rounded-[var(--radius)] border border-border px-3 py-2">
                  <StatusDot state="online" />
                  <strong className="text-[13px] text-ink">{s.current_label || 'agent'}</strong>
                  <span className="text-[12px] text-ink-3">running…</span>
                </div>
              )}
              {(runs.data?.runs ?? []).slice(0, 8).map((r, i) => (
                <div key={i} className="flex items-center gap-2.5 border-b border-border px-2 py-2 last:border-0">
                  <StatusDot state={r.cancelled ? 'disabled' : r.ok ? 'online' : r.error ? 'offline' : 'unknown'} />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <strong className="truncate text-[13px] text-ink">{r.label || r.id}</strong>
                      {r.cost_usd != null && <span className="text-[11.5px] text-ink-3">${r.cost_usd}</span>}
                    </div>
                    <div className="truncate text-[11.5px] text-ink-3">{(r.result || r.error || '').slice(0, 100)}</div>
                  </div>
                  <span className="text-[11px] text-ink-3">{(r.started || '').replace('T', ' ').slice(5, 16)}</span>
                  {r.id && (
                    <>
                      <Button
                        variant="ghost"
                        size="sm"
                        title="See the full transcript: messages, tool calls and results"
                        onClick={() => setOpenRun(r.id!)}
                      >
                        View
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        title="Open the wizard prefilled from this run"
                        disabled={s?.running || rerunning === r.id}
                        onClick={() => rerun(r.id!)}
                      >
                        <RotateCw size={13} />
                      </Button>
                    </>
                  )}
                </div>
              ))}
              {!s?.running && (runs.data?.runs ?? []).length === 0 && (
                <EmptyState icon={Bot} title="No agent runs yet" hint="Launch an agent with “New agent” to see runs and their output here." />
              )}
            </div>
          </Card>

          <Card>
            <CardHeader title="Scheduled jobs" />
            <div className="px-2 pb-2">
              {(schedules.data?.schedules ?? []).filter((x) => x.kind === 'agent' || x.kind === 'task').length === 0 ? (
                <EmptyState icon={CalendarClock} title="No scheduled agents" hint="Pick a schedule in the launch wizard to run an agent on a cron." />
              ) : (
                (schedules.data?.schedules ?? [])
                  .filter((x) => x.kind === 'agent' || x.kind === 'task')
                  .map((sc) => (
                    <div key={sc.id} className="flex flex-wrap items-center gap-2 border-b border-border px-2 py-2 last:border-0">
                      <StatusDot state={sc.enabled ? 'online' : 'disabled'} />
                      <strong className="text-[13px] text-ink">{sc.name}</strong>
                      <code className="font-mono text-[11.5px] text-ink-3">{sc.cron}</code>
                      <span className="text-[11.5px] text-ink-3">
                        next {sc.next_run ? sc.next_run.replace('T', ' ').slice(0, 16) : '—'}
                      </span>
                      <div className="ml-auto flex items-center gap-1">
                        <Button
                          variant="ghost"
                          size="sm"
                          aria-label={sc.enabled ? 'Pause schedule' : 'Resume schedule'}
                          onClick={async () => {
                            await api.post(`/api/v1/schedules/${sc.id}`, { ...sc, enabled: !sc.enabled })
                            schedules.refetch()
                          }}
                        >
                          {sc.enabled ? <Pause size={13} /> : <Play size={13} />}
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => api.post(`/api/v1/schedules/${sc.id}/run-now`).then(() => startStream())}
                        >
                          Run
                        </Button>
                        <Button
                          variant="danger"
                          size="icon-sm"
                          aria-label="Delete schedule"
                          onClick={async () => {
                            if (!confirm('Delete schedule?')) return
                            await api.del(`/api/v1/schedules/${sc.id}`)
                            schedules.refetch()
                          }}
                        >
                          <Trash2 size={13} />
                        </Button>
                      </div>
                    </div>
                  ))
              )}
            </div>
          </Card>

          {console.length > 0 && (
            <Card>
              <CardHeader title="Live console" />
              <pre className="max-h-80 overflow-auto whitespace-pre-wrap px-4 py-3 font-mono text-[11.5px] text-ink-2">
                {console.join('\n')}
              </pre>
            </Card>
          )}
        </div>
      </PageBody>
      {openRun && <RunTranscriptModal runId={openRun} onClose={() => setOpenRun(null)} />}
    </>
  )
}

const isPublic = (s: Service) => (s.section || '').toLowerCase().includes('public')

/** Self-hosted connections start on; public/internet ones start off, so an agent
 *  only reaches the internet when you deliberately tick it. */
const defaultOn = (s: Service) => !isPublic(s)

/** Self-hosted first, then public — the picker reads as two groups. */
const orderConnections = (list: Service[]) =>
  [...list].sort((a, b) => Number(isPublic(a)) - Number(isPublic(b)) || a.label.localeCompare(b.label))

function LaunchWizard({
  connections,
  initial,
  onLaunched,
  onScheduled,
}: {
  connections: Service[]
  initial?: RunPrefill | null
  onLaunched: () => void
  onScheduled: () => void
}) {
  const [name, setName] = useState(initial?.label ?? '')
  const [model, setModel] = useState(initial?.model ?? '')
  const [prompt, setPrompt] = useState(initial?.prompt ?? '')
  const [sched, setSched] = useState('now')
  const [time, setTime] = useState('07:00')
  const [dow, setDow] = useState('1')
  const [cron, setCron] = useState('0 7 * * *')
  const [account, setAccount] = useState(
    initial?.provider && initial?.account_id ? `${initial.provider}/${initial.account_id}` : '',
  )
  const providersQ = useProviders()
  const linkedAccounts = toLinked(providersQ.data?.providers)
  // Which runtime this launch will use — the model menu follows it. With no
  // account picked that is the legacy single login, which is Claude.
  const { provider, accountId } = splitAccount(account)
  const providerLabel =
    (providersQ.data?.providers ?? []).find((p) => p.id === provider)?.label ?? provider
  // Access level and timeout are no longer per-launch choices — they come from
  // Settings → Agent (tool_permission / timeout_min). The connection picker is
  // the single control over what an agent may touch.
  // A repeated run starts from the scope it actually ran with. `null` means the
  // original had no restriction, which is not the same as an empty selection.
  const [selected, setSelected] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(
      connections.map((c) => [
        c.id,
        initial?.mcp_services ? initial.mcp_services.includes(c.id) : defaultOn(c),
      ]),
    ),
  )
  // Connections may resolve after this wizard mounts; default any newly
  // arrived ones without clobbering the user's toggles.
  useEffect(() => {
    setSelected((prev) => {
      let changed = false
      const next = { ...prev }
      for (const c of connections) {
        if (!(c.id in next)) {
          next[c.id] = initial?.mcp_services ? initial.mcp_services.includes(c.id) : defaultOn(c)
          changed = true
        }
      }
      return changed ? next : prev
    })
  }, [connections])
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)

  async function launch() {
    if (!prompt.trim()) {
      setMsg('Enter a goal/prompt.')
      return
    }
    setBusy(true)
    setMsg('…')
    try {
      const mcpServices = Object.entries(selected)
        .filter(([, v]) => v)
        .map(([k]) => k)
      // The account is only sent when one was actually picked; "" keeps the
      // legacy single-login path. The model rides along *with the run* — it used
      // to be POSTed into the global agent config, so one Opus launch quietly
      // pinned Opus on every later run, including ones on other providers.
      const picked = account ? { provider, account_id: accountId } : { provider: '', account_id: '' }
      const cronExpr = cronFrom(sched, time, dow, cron)
      if (cronExpr) {
        await api.post('/api/v1/schedules', {
          name: name || 'agent',
          kind: 'agent',
          cron: cronExpr,
          timezone: 'Europe/Berlin',
          enabled: true,
          payload: { prompt: prompt.trim(), mcp_services: mcpServices, ...picked, model: model.trim() },
        })
        onScheduled()
      } else {
        await api.post('/api/v1/agent/run', {
          prompt: prompt.trim(),
          label: name || 'agent',
          mcp_services: mcpServices,
          ...picked,
          model: model.trim(),
        })
        onLaunched()
      }
    } catch (e) {
      setMsg(String(e))
      setBusy(false)
    }
  }

  return (
    <Card className="border-l-2 border-l-accent">
      <CardHeader title="Launch an agent" />
      <div className="space-y-3 px-4 pb-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Name">
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Morning research" />
          </Field>
          {/* Same field, same place — only the options change, and they now
              follow the selected account's provider instead of always listing
              Claude's models. */}
          <Field label="Model" hint={`Offered by ${providerLabel}`}>
            <ModelPicker
              key={`${provider}/${accountId}`}
              provider={provider}
              accountId={accountId}
              value={model}
              onChange={setModel}
            />
          </Field>
        </div>
        {linkedAccounts.length > 0 && (
          <Field
            label="Account"
            hint="Which linked account runs this agent. Codex is a coding runtime, Gemini a research one — pick to match the task. Manage accounts in Settings → AI providers."
          >
            <Select
              value={account}
              onChange={(e) => {
                setAccount(e.target.value)
                // A model id belongs to one provider: keeping 'opus' selected
                // while switching to Codex would send Codex a model it has never
                // heard of.
                setModel('')
              }}
            >
              <option value="">Default login</option>
              {linkedAccounts.map((a) => (
                <option key={`${a.provider}/${a.id}`} value={`${a.provider}/${a.id}`}>
                  {a.providerLabel} · {a.label}
                  {a.role ? ` (${a.role})` : ''}
                </option>
              ))}
            </Select>
          </Field>
        )}
        <Field label="Goal / prompt">
          <Textarea rows={3} value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="What should the agent do?" />
        </Field>
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Schedule">
            <Select value={sched} onChange={(e) => setSched(e.target.value)}>
              <option value="now">Run now</option>
              <option value="daily">Daily</option>
              <option value="weekly">Weekly</option>
              <option value="cron">Custom cron</option>
            </Select>
          </Field>
          {(sched === 'daily' || sched === 'weekly') && (
            <Field label="Time">
              <Input type="time" value={time} onChange={(e) => setTime(e.target.value)} />
            </Field>
          )}
          {sched === 'weekly' && (
            <Field label="Day">
              <Select value={dow} onChange={(e) => setDow(e.target.value)}>
                {['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((d, i) => (
                  <option key={d} value={i === 6 ? '0' : String(i + 1)}>
                    {d}
                  </option>
                ))}
              </Select>
            </Field>
          )}
          {sched === 'cron' && (
            <Field label="Cron">
              <Input value={cron} onChange={(e) => setCron(e.target.value)} />
            </Field>
          )}
        </div>
        {connections.length > 0 && (
          <Field
            label="MCP connections the agent may use"
            hint="Public services (web search, weather, Wikipedia…) are listed but off by default — tick them to let the agent reach the internet."
          >
            <ConnectionPicker connections={connections} selected={selected} onChange={setSelected} />
          </Field>
        )}
        <div className="flex items-center gap-3">
          <Button variant="primary" size="sm" disabled={busy} onClick={launch}>
            Launch
          </Button>
          {msg && <span className="text-[12px] text-ink-3">{msg}</span>}
        </div>
      </div>
    </Card>
  )
}
