import { useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ExternalLink, Plus, RefreshCw, Search, Settings2, Ban, RotateCcw } from 'lucide-react'
import { api } from '@/lib/api'
import { serviceHealth, type HealthState, type Service } from '@/lib/health'
import { PageHead, PageBody } from '@/components/PageHead'
import { Card } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Input, Select } from '@/components/ui/Input'
import { Tag } from '@/components/ui/Tag'
import { HealthBadge } from '@/components/ui/StatusDot'
import { ServiceLogo } from '@/components/ui/ServiceLogo'
import { ConfigureModal } from '@/components/connections/ConfigureModal'
import { AddConnectionModal } from '@/components/connections/AddConnectionModal'

const STATUS_FILTERS: { value: string; label: string }[] = [
  { value: '', label: 'All statuses' },
  { value: 'online', label: 'Online' },
  { value: 'offline', label: 'Offline' },
  { value: 'unconfigured', label: 'Not configured' },
  { value: 'disabled', label: 'Disabled' },
]

export function Connections() {
  const qc = useQueryClient()
  const { data, isLoading, isError } = useQuery({
    queryKey: ['services'],
    queryFn: () => api.get<{ services?: Service[] }>('/api/v1/dashboard?sections=services'),
  })
  const [q, setQ] = useState('')
  const [status, setStatus] = useState('')
  const [hideUnconfigured, setHideUnconfigured] = useState(false)
  const [showIgnored, setShowIgnored] = useState(false)
  const [busy, setBusy] = useState<Record<string, string>>({})
  const [configureId, setConfigureId] = useState<string | null>(null)
  const [addOpen, setAddOpen] = useState(false)
  const [report, setReport] = useState<string | null>(null)

  const services = data?.services ?? []
  const refresh = () => qc.invalidateQueries({ queryKey: ['services'] })

  const rows = useMemo(() => {
    const ql = q.toLowerCase()
    return services.filter((s) => {
      if (s.ignored && !showIgnored) return false
      if (hideUnconfigured && !s.configured) return false
      const st = serviceHealth(s)
      if (status && st !== status) return false
      if (ql && !`${s.label} ${s.id} ${s.section} ${s.tag} ${s.url}`.toLowerCase().includes(ql)) return false
      return true
    })
  }, [services, q, status, hideUnconfigured, showIgnored])

  const active = services.filter((s) => !s.ignored)

  async function act(id: string, kind: string, fn: () => Promise<unknown>) {
    setBusy((b) => ({ ...b, [id]: kind }))
    try {
      await fn()
      refresh()
    } catch (e) {
      alert(String(e))
    } finally {
      setBusy((b) => {
        const n = { ...b }
        delete n[id]
        return n
      })
    }
  }

  async function testAll(btn: HTMLButtonElement) {
    btn.disabled = true
    try {
      const d = await api.post<{ markdown?: string }>('/health/full-report')
      setReport(d.markdown || '(no report)')
      refresh()
    } catch (e) {
      alert(String(e))
    } finally {
      btn.disabled = false
    }
  }

  return (
    <>
      <PageHead
        title="Connections"
        subtitle="Every service this MCP server can reach"
        actions={
          <>
            <Button variant="ghost" size="sm" onClick={() => refresh()}>
              <RefreshCw size={14} /> Refresh
            </Button>
            <Button variant="ghost" size="sm" onClick={(e) => testAll(e.currentTarget)}>
              Test all
            </Button>
            <Button variant="primary" size="sm" onClick={() => setAddOpen(true)}>
              <Plus size={14} /> Add
            </Button>
          </>
        }
      />
      <PageBody>
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <div className="relative">
            <Search size={14} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-3" />
            <Input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search…"
              className="h-8 w-56 pl-8"
            />
          </div>
          <Select value={status} onChange={(e) => setStatus(e.target.value)} className="w-40">
            {STATUS_FILTERS.map((f) => (
              <option key={f.value} value={f.value}>
                {f.label}
              </option>
            ))}
          </Select>
          <label className="flex items-center gap-1.5 text-[12.5px] text-ink-2">
            <input type="checkbox" checked={hideUnconfigured} onChange={(e) => setHideUnconfigured(e.target.checked)} />
            Hide unconfigured
          </label>
          <label className="flex items-center gap-1.5 text-[12.5px] text-ink-2">
            <input type="checkbox" checked={showIgnored} onChange={(e) => setShowIgnored(e.target.checked)} />
            Show ignored
          </label>
          <span className="ml-auto text-[12px] text-ink-3">
            {rows.length} shown · {active.length} active
          </span>
        </div>

        <Card className="overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-[13px]">
              <thead>
                <tr className="border-b border-border text-left text-[11px] uppercase tracking-wide text-ink-3">
                  <th className="px-3 py-2 font-semibold">Service</th>
                  <th className="px-3 py-2 font-semibold">Category</th>
                  <th className="px-3 py-2 text-right font-semibold">Tools</th>
                  <th className="px-3 py-2 font-semibold">Address</th>
                  <th className="px-3 py-2 font-semibold">Status</th>
                  <th className="px-3 py-2 text-right font-semibold">Actions</th>
                </tr>
              </thead>
              <tbody>
                {isLoading ? (
                  <tr>
                    <td colSpan={6} className="px-3 py-6 text-ink-3">
                      Loading…
                    </td>
                  </tr>
                ) : isError ? (
                  <tr>
                    <td colSpan={6} className="px-3 py-6 text-danger">
                      Couldn’t load connections.
                    </td>
                  </tr>
                ) : rows.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="px-3 py-6 text-ink-3">
                      No connections match.
                    </td>
                  </tr>
                ) : (
                  rows.map((s) => {
                    const st = serviceHealth(s)
                    const host = (s.url || '').replace(/^https?:\/\//, '')
                    return (
                      <tr
                        key={s.id}
                        className={`border-b border-border last:border-0 hover:bg-surface-hover ${s.ignored ? 'opacity-45' : ''}`}
                      >
                        <td className="px-3 py-2.5">
                          <div className="flex items-center gap-2.5">
                            <ServiceLogo id={s.id} label={s.label} />
                            <div className="min-w-0">
                              <div className="truncate font-medium text-ink">{s.label}</div>
                              <div className="truncate text-[11.5px] text-ink-3">{s.id}</div>
                            </div>
                          </div>
                        </td>
                        <td className="px-3 py-2.5">
                          <Tag>{s.tag || s.section || '—'}</Tag>
                        </td>
                        <td className="px-3 py-2.5 text-right font-mono text-[12px] text-ink-2">{s.tool_count ?? 0}</td>
                        <td className="px-3 py-2.5">
                          {s.url ? (
                            <a
                              href={s.url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="inline-flex items-center gap-1 font-mono text-[12px] text-ink-2 hover:text-accent"
                              title={s.url}
                            >
                              {host.length > 30 ? host.slice(0, 29) + '…' : host}
                              <ExternalLink size={11} />
                            </a>
                          ) : (
                            <span className="text-ink-3">—</span>
                          )}
                        </td>
                        <td className="px-3 py-2.5">
                          <HealthBadge state={st as HealthState} />
                        </td>
                        <td className="px-3 py-2.5">
                          <div className="flex items-center justify-end gap-1">
                            <Button
                              variant="ghost"
                              size="sm"
                              disabled={!!busy[s.id]}
                              onClick={() => act(s.id, 'test', () => api.get(`/service/test/${s.id}`))}
                              title="HTTP reachability probe"
                            >
                              {busy[s.id] === 'test' ? '…' : 'Test'}
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              disabled={!!busy[s.id]}
                              onClick={() => act(s.id, 'try', () => api.post(`/service/smoke-tools/${s.id}`))}
                              title="Call the tools and check they return data"
                            >
                              {busy[s.id] === 'try' ? '…' : 'Try'}
                            </Button>
                            <Button variant="ghost" size="icon-sm" title="Configure" onClick={() => setConfigureId(s.id)}>
                              <Settings2 size={14} />
                            </Button>
                            <Button
                              variant="ghost"
                              size="icon-sm"
                              title={s.ignored ? 'Restore' : 'Ignore'}
                              onClick={() =>
                                act(s.id, 'ignore', () =>
                                  api.post(`/api/v1/service/${s.id}/ignore`, { ignored: !s.ignored }),
                                )
                              }
                            >
                              {s.ignored ? <RotateCcw size={14} /> : <Ban size={14} />}
                            </Button>
                          </div>
                        </td>
                      </tr>
                    )
                  })
                )}
              </tbody>
            </table>
          </div>
        </Card>
      </PageBody>

      {configureId && (
        <ConfigureModal
          id={configureId}
          onClose={() => setConfigureId(null)}
          onSaved={() => {
            setConfigureId(null)
            refresh()
          }}
        />
      )}
      {addOpen && (
        <AddConnectionModal
          onClose={() => setAddOpen(false)}
          onAdded={() => {
            setAddOpen(false)
            refresh()
          }}
        />
      )}
      {report !== null && (
        <ReportModal report={report} onClose={() => setReport(null)} />
      )}
    </>
  )
}

function ReportModal({ report, onClose }: { report: string; onClose: () => void }) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/45 p-4 pt-[8vh]"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="w-full max-w-[720px] rounded-[var(--radius-lg)] border border-border bg-surface shadow-[var(--shadow-pop)]">
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <h2 className="text-[15px] font-semibold text-ink">Health report</h2>
          <Button variant="ghost" size="sm" onClick={onClose}>
            Close
          </Button>
        </div>
        <pre className="max-h-[70vh] overflow-auto whitespace-pre-wrap px-4 py-4 font-mono text-[12px] text-ink-2">
          {report}
        </pre>
      </div>
    </div>
  )
}
