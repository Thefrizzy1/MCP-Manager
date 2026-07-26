import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Check, CircleCheck, Copy, Cpu, TriangleAlert } from 'lucide-react'
import { api } from '@/lib/api'
import { navigate } from '@/lib/router'
import { serviceHealth, type Service } from '@/lib/health'
import { PageHead, PageBody } from '@/components/PageHead'
import { Card, CardHeader } from '@/components/ui/Card'
import { Stat } from '@/components/ui/Stat'
import { HealthBadge } from '@/components/ui/StatusDot'
import { OptimizePanel } from '@/components/dashboard/OptimizePanel'

interface DashboardPayload {
  main?: { registered_tools?: number; capabilities?: number }
  networking?: { http_local?: string }
  services?: Service[]
  recent_tool_runs?: Array<{ tool?: string; ts?: string } | string>
}

function EndpointBar({ url }: { url: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <div className="flex items-center gap-2.5 rounded-[var(--radius)] border border-border bg-surface px-3 py-2">
      <span className="text-[11px] font-medium uppercase tracking-wide text-ink-3">MCP endpoint</span>
      <code className="min-w-0 truncate font-mono text-[12.5px] text-ink">{url}</code>
      <button
        aria-label="Copy MCP endpoint"
        onClick={() => {
          navigator.clipboard?.writeText(url)
          setCopied(true)
          setTimeout(() => setCopied(false), 1500)
        }}
        className="ml-auto flex shrink-0 items-center gap-1 text-[12px] font-medium text-accent hover:underline"
      >
        {copied ? <Check size={13} /> : <Copy size={13} />}
        {copied ? 'Copied' : 'Copy'}
      </button>
    </div>
  )
}
interface AgentStatus {
  auth?: { mode?: string }
  runs_today?: number
  max_runs_per_day?: number
}

const ATTENTION = new Set(['offline', 'auth_error', 'api_error', 'rate_limited', 'unconfigured'])

export function Dashboard() {
  const dash = useQuery({
    queryKey: ['dashboard'],
    queryFn: () => api.get<DashboardPayload>('/api/v1/dashboard?sections=main,services,recent,networking'),
  })
  const agent = useQuery({
    queryKey: ['agent-status'],
    queryFn: () => api.get<AgentStatus>('/api/v1/agent/status'),
    retry: 0,
  })

  const services = (dash.data?.services ?? []).filter((s) => !s.ignored)
  const online = services.filter((s) => serviceHealth(s) === 'online')
  const attention = services.filter((s) => ATTENTION.has(serviceHealth(s)))
  const tools = dash.data?.main?.registered_tools ?? 0
  const caps = dash.data?.main?.capabilities ?? 0

  const mode = agent.data?.auth?.mode ?? 'none'
  const aiConnected = mode === 'session_token' || mode === 'subscription'
  const aiTone = aiConnected ? 'ok' : mode === 'api_key' ? 'warn' : 'danger'
  const aiValue = aiConnected ? 'Claude' : mode === 'api_key' ? 'API' : 'Off'
  const aiHint = aiConnected ? 'Subscription connected' : mode === 'api_key' ? 'API billing' : 'No provider connected'

  const runs = (dash.data?.recent_tool_runs ?? []).slice().reverse().slice(0, 10)

  return (
    <>
      <PageHead title="Dashboard" subtitle="Everything Plutus can reach, at a glance" />
      <PageBody>
        {dash.isLoading ? (
          <p className="text-[13px] text-ink-3">Loading…</p>
        ) : dash.isError ? (
          <Card className="p-4 text-[13px] text-danger">Couldn’t load the dashboard.</Card>
        ) : (
          <div className="space-y-5">
            {dash.data?.networking?.http_local && <EndpointBar url={dash.data.networking.http_local} />}
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <Stat
                label="Connected"
                value={`${online.length}/${services.length}`}
                hint="services online"
                tone="ok"
                onClick={() => navigate('connections')}
              />
              <Stat
                label="Needs attention"
                value={attention.length}
                hint={attention.length ? 'offline or unconfigured' : 'all clear'}
                tone={attention.length ? 'danger' : 'ok'}
                onClick={() => navigate('connections')}
              />
              <Stat label="Tools exposed" value={tools} hint={`${caps} capabilities`} />
              <Stat label="AI provider" value={aiValue} hint={aiHint} tone={aiTone} onClick={() => navigate('agents')} />
            </div>

            <div className="grid gap-5 lg:grid-cols-2">
              <Card>
                <CardHeader
                  title="Needs attention"
                  subtitle={attention.length ? `${attention.length} connection(s)` : undefined}
                />
                <div className="px-2 pb-2">
                  {attention.length === 0 ? (
                    <div className="flex items-center gap-2 px-2 py-3 text-[13px] text-ink-3">
                      <CircleCheck size={16} className="text-ok" />
                      Everything is online.
                    </div>
                  ) : (
                    attention.map((s) => (
                      <button
                        key={s.id}
                        onClick={() => navigate('connections')}
                        className="flex w-full items-center gap-2.5 rounded-[var(--radius-sm)] px-2 py-2 text-left hover:bg-surface-hover"
                      >
                        <TriangleAlert size={15} className="shrink-0 text-warn" />
                        <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-ink">{s.label}</span>
                        <HealthBadge state={serviceHealth(s)} />
                      </button>
                    ))
                  )}
                </div>
              </Card>

              <Card>
                <CardHeader title="Recent activity" />
                <div className="px-2 pb-2">
                  {runs.length === 0 ? (
                    <p className="px-2 py-3 text-[13px] text-ink-3">No recent tool runs.</p>
                  ) : (
                    runs.map((r, i) => {
                      const tool = typeof r === 'string' ? r : r.tool
                      const ts = typeof r === 'string' ? '' : r.ts
                      return (
                        <div
                          key={i}
                          className="flex items-center gap-2 border-b border-border px-2 py-2 last:border-0"
                        >
                          <Cpu size={14} className="shrink-0 text-ink-3" />
                          <code className="min-w-0 flex-1 truncate font-mono text-[12px] text-ink-2">{tool}</code>
                          <span className="text-[11px] text-ink-3">{ts}</span>
                        </div>
                      )
                    })
                  )}
                </div>
              </Card>
            </div>

            <OptimizePanel />
          </div>
        )}
      </PageBody>
    </>
  )
}
