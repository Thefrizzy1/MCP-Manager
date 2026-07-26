import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Square, Play, Pause, Trash2 } from 'lucide-react'
import { api } from '@/lib/api'
import type { Service } from '@/lib/health'
import { PageHead, PageBody } from '@/components/PageHead'
import { Card, CardHeader } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Input, Select, Textarea } from '@/components/ui/Input'
import { Field } from '@/components/ui/Field'
import { Stat } from '@/components/ui/Stat'
import { StatusDot } from '@/components/ui/StatusDot'

interface AgentStatus {
  auth?: { mode?: string }
  running?: boolean
  current_label?: string
  runs_today?: number
  max_runs_per_day?: number
  queue_depth?: number
  total_cost_usd?: number
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

  const s = status.data
  const cap = s?.max_runs_per_day ?? 0
  const used = s?.runs_today ?? 0
  const mode = s?.auth?.mode ?? 'none'
  const onPlan = mode === 'session_token' || mode === 'subscription'

  return (
    <>
      <PageHead
        title="Agents"
        subtitle="Launch, schedule and monitor headless agents"
        actions={
          <Button variant="primary" size="sm" onClick={() => setWizard((w) => !w)}>
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
              label="Provider"
              value={onPlan ? 'Plan' : mode === 'api_key' ? 'API' : 'Off'}
              tone={onPlan ? 'ok' : mode === 'api_key' ? 'warn' : 'danger'}
            />
          </div>

          {wizard && (
            <LaunchWizard
              connections={(conns.data?.services ?? []).filter(
                (x) => x.configured && !x.ignored && !(x.section || '').toLowerCase().includes('public'),
              )}
              onLaunched={() => {
                setWizard(false)
                startStream()
                status.refetch()
              }}
              onScheduled={() => {
                setWizard(false)
                qc.invalidateQueries({ queryKey: ['schedules'] })
              }}
            />
          )}

          <Card>
            <CardHeader
              title="Running & recent"
              action={
                s?.running && (
                  <Button variant="danger" size="sm" onClick={() => api.post('/api/v1/agent/cancel').then(() => status.refetch())}>
                    <Square size={13} /> Stop
                  </Button>
                )
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
                </div>
              ))}
              {!s?.running && (runs.data?.runs ?? []).length === 0 && (
                <p className="px-2 py-3 text-[13px] text-ink-3">No runs yet.</p>
              )}
            </div>
          </Card>

          <Card>
            <CardHeader title="Scheduled jobs" />
            <div className="px-2 pb-2">
              {(schedules.data?.schedules ?? []).filter((x) => x.kind === 'agent' || x.kind === 'task').length === 0 ? (
                <p className="px-2 py-3 text-[13px] text-ink-3">No scheduled agents.</p>
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
    </>
  )
}

function LaunchWizard({
  connections,
  onLaunched,
  onScheduled,
}: {
  connections: Service[]
  onLaunched: () => void
  onScheduled: () => void
}) {
  const [name, setName] = useState('')
  const [model, setModel] = useState('')
  const [prompt, setPrompt] = useState('')
  const [sched, setSched] = useState('now')
  const [time, setTime] = useState('07:00')
  const [dow, setDow] = useState('1')
  const [cron, setCron] = useState('0 7 * * *')
  const [perm, setPerm] = useState('safe')
  const [timeout, setTimeout] = useState(20)
  const [selected, setSelected] = useState<Record<string, boolean>>(
    Object.fromEntries(connections.map((c) => [c.id, true])),
  )
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
      await api.post('/api/v1/agent/config', { timeout_min: timeout || 20, model: model.trim() })
      const cronExpr = cronFrom(sched, time, dow, cron)
      if (cronExpr) {
        await api.post('/api/v1/schedules', {
          name: name || 'agent',
          kind: 'agent',
          cron: cronExpr,
          timezone: 'Europe/Berlin',
          enabled: true,
          payload: { prompt: prompt.trim(), permission: perm, mcp_services: mcpServices },
        })
        onScheduled()
      } else {
        await api.post('/api/v1/agent/run', {
          prompt: prompt.trim(),
          label: name || 'agent',
          permission: perm,
          mcp_services: mcpServices,
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
          <Field label="Model">
            <Input value={model} onChange={(e) => setModel(e.target.value)} placeholder="default (opus / sonnet / haiku)" />
          </Field>
        </div>
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
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="MCP access level">
            <Select value={perm} onChange={(e) => setPerm(e.target.value)}>
              <option value="strict_read">Strict read</option>
              <option value="safe">Safe (reads + notes)</option>
              <option value="all">All tools</option>
            </Select>
          </Field>
          <Field label="Timeout (min)">
            <Input type="number" value={timeout} min={1} max={120} onChange={(e) => setTimeout(parseInt(e.target.value, 10) || 20)} />
          </Field>
        </div>
        {connections.length > 0 && (
          <Field label="MCP connections the agent may use">
            <div className="flex flex-wrap gap-1.5">
              {connections.map((c) => (
                <label
                  key={c.id}
                  className="flex cursor-pointer items-center gap-1.5 rounded-full border border-border bg-surface-2 px-2 py-1 text-[12px] text-ink-2"
                >
                  <input
                    type="checkbox"
                    checked={!!selected[c.id]}
                    onChange={(e) => setSelected((sv) => ({ ...sv, [c.id]: e.target.checked }))}
                  />
                  {c.label}
                </label>
              ))}
            </div>
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
